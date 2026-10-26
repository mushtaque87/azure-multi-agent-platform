"""Azure SQL serverless setup for the house-price agents.

Portal first (see docs/concepts/01-foundation.md), then this script automates the same steps:

  python infra/scripts/sql_setup.py --create          # RG, budget tags, Entra-only server, serverless DB, firewall
  python infra/scripts/sql_setup.py --load            # create dbo.house_prices, load 10,000 rows, verify
  python infra/scripts/sql_setup.py --reader <name>   # make a managed identity a db_datareader user (Phase 3)

Authentication is Entra ID only: the script uses your `az login` token, so there is no SQL password anywhere.
"""
import argparse
import os
import struct
import sys
import urllib.request

from azcli import az, exists, load_state, save_state, step

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CSV = os.path.join(ROOT, "data", "house_prices.csv")
TAGS = ["project=house-agents", "owner=mushtaque", "env=dev"]
SQL_COPT_SS_ACCESS_TOKEN = 1256  # ODBC attribute for passing an Entra access token

DDL = """
IF OBJECT_ID('dbo.house_prices') IS NULL
CREATE TABLE dbo.house_prices (
    id            INT          NOT NULL PRIMARY KEY,
    sqft          INT          NOT NULL,
    bedrooms      INT          NOT NULL,
    bathrooms     INT          NOT NULL,
    age_years     INT          NOT NULL,
    garage_spaces INT          NOT NULL,
    neighborhood  VARCHAR(10)  NOT NULL,
    condition     VARCHAR(9)   NOT NULL,
    has_pool      BIT          NOT NULL,
    lot_size_sqft INT          NOT NULL,
    month_sold    INT          NOT NULL,
    year_sold     INT          NOT NULL,
    price         BIGINT       NOT NULL
);
"""


def parse_args(state):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-g", "--resource-group", default=state.get("resource_group", "rg-house-agents"))
    p.add_argument("-s", "--server", default=state.get("sql_server", "sql-house-agents"))
    p.add_argument("-d", "--database", default=state.get("sql_database", "housepricedb"))
    p.add_argument("-l", "--location", default=state.get("location", "uaenorth"))
    p.add_argument("--create", action="store_true", help="create RG, server and serverless database")
    p.add_argument("--load", action="store_true", help="create the table and load the CSV")
    p.add_argument("--reader", metavar="IDENTITY_NAME", help="grant db_datareader to this Entra identity")
    return p.parse_args()


def my_public_ip():
    with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
        return r.read().decode().strip()


def create(a):
    step(f"Resource group {a.resource_group}")
    az("group", "create", "-n", a.resource_group, "-l", a.location, "--tags", *TAGS, parse=False)

    step(f"SQL server {a.server} (Entra-only, you as admin)")
    if exists("sql", "server", "show", "-n", a.server, "-g", a.resource_group):
        print("   exists")
    else:
        me = az("ad", "signed-in-user", "show")
        az("sql", "server", "create", "-n", a.server, "-g", a.resource_group, "-l", a.location,
           "--enable-ad-only-auth", "--external-admin-principal-type", "User",
           "--external-admin-name", me["userPrincipalName"], "--external-admin-sid", me["id"], parse=False)

    step("Firewall: your IP + Azure services")
    ip = my_public_ip()
    az("sql", "server", "firewall-rule", "create", "-s", a.server, "-g", a.resource_group,
       "-n", "my-ip", "--start-ip-address", ip, "--end-ip-address", ip, parse=False)
    # 0.0.0.0 is the special "allow Azure services" rule (needed by Container Apps until private endpoints)
    az("sql", "server", "firewall-rule", "create", "-s", a.server, "-g", a.resource_group,
       "-n", "AllowAllWindowsAzureIps", "--start-ip-address", "0.0.0.0", "--end-ip-address", "0.0.0.0", parse=False)

    step(f"Database {a.database} (General Purpose serverless, 0.5-1 vCore, auto-pause 60 min)")
    if exists("sql", "db", "show", "-n", a.database, "-s", a.server, "-g", a.resource_group):
        print("   exists")
    else:
        az("sql", "db", "create", "-n", a.database, "-s", a.server, "-g", a.resource_group,
           "--edition", "GeneralPurpose", "--compute-model", "Serverless", "--family", "Gen5",
           "--capacity", "1", "--min-capacity", "0.5", "--auto-pause-delay", "60",
           "--max-size", "1GB", "--backup-storage-redundancy", "Local", "--tags", *TAGS, parse=False)


def connect(a):
    """Connect with an Entra token - no password involved.

    Inside the tools container there is no `az login`, so run_tools.sh passes a short-lived token
    from the host in SQL_ACCESS_TOKEN. Otherwise the token comes from the local Azure CLI.
    """
    import pyodbc  # only needed for --load/--reader, which run in the tools container

    raw = os.environ.get("SQL_ACCESS_TOKEN")
    if not raw:
        from azure.identity import AzureCliCredential
        raw = AzureCliCredential().get_token("https://database.windows.net/.default").token
    token = raw.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    conn_str = (f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{a.server}.database.windows.net,1433;"
                f"Database={a.database};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=90")
    # A paused serverless DB takes up to a minute to resume, hence the long timeout
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def load(a):
    import pandas as pd

    step("Connecting (first connect may wait ~1 min if the database is auto-paused)")
    conn = connect(a)
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute("SELECT COUNT(*) FROM dbo.house_prices")
    if cur.fetchone()[0] == 0:
        df = pd.read_csv(CSV)
        df.columns = [c.lower() for c in df.columns]
        df["has_pool"] = df["has_pool"].astype(str).str.lower().isin(["true", "1"]).astype(int)
        cols = ["id", "sqft", "bedrooms", "bathrooms", "age_years", "garage_spaces", "neighborhood",
                "condition", "has_pool", "lot_size_sqft", "month_sold", "year_sold", "price"]
        step(f"Loading {len(df)} rows")
        cur.fast_executemany = True
        cur.executemany(f"INSERT INTO dbo.house_prices ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        df[cols].astype(object).values.tolist())
        conn.commit()
    else:
        print("   table already loaded")

    step("Verify")
    cur.execute("SELECT COUNT(*), (SELECT ROUND(AVG(CAST(price AS FLOAT)), 0) FROM dbo.house_prices "
                "WHERE neighborhood = 'Downtown') FROM dbo.house_prices")
    rows, downtown = cur.fetchone()
    print(f"   rows={rows}  downtown_avg={downtown:,.0f}  (Snowflake: 10000 / 770,570)")
    conn.close()


def grant_reader(a, identity):
    step(f"Granting db_datareader to {identity}")
    conn = connect(a)
    cur = conn.cursor()
    # Identity names can't be bound as parameters in DDL, so validate before formatting
    if not identity.replace("-", "").replace("_", "").isalnum():
        sys.exit("Identity name may only contain letters, digits, '-' and '_'")
    cur.execute(f"IF DATABASE_PRINCIPAL_ID('{identity}') IS NULL CREATE USER [{identity}] FROM EXTERNAL PROVIDER;")
    cur.execute(f"ALTER ROLE db_datareader ADD MEMBER [{identity}];")
    conn.commit()
    print("   done (read-only: SELECT allowed, INSERT/UPDATE/DELETE/DROP denied)")
    conn.close()


if __name__ == "__main__":
    args = parse_args(load_state())
    state = load_state()
    state.update(resource_group=args.resource_group, sql_server=args.server,
                 sql_database=args.database, location=args.location)
    save_state(state)
    if args.create:
        create(args)
    if args.load:
        load(args)
    if args.reader:
        grant_reader(args, args.reader)
    if not (args.create or args.load or args.reader):
        print("Nothing to do. Use --create, --load and/or --reader. See --help.")
