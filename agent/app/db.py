"""Azure SQL access with Entra ID tokens - no password.

Token source, in order:
  1. SQL_ACCESS_TOKEN env var (local dev: run_agent.sh passes a short-lived token from `az`)
  2. DefaultAzureCredential (in Azure: the Container App's managed identity)
"""
import os
import struct  # pack the token into the binary layout the ODBC driver expects
import time

import pyodbc  # Python wrapper around the Microsoft ODBC Driver 18 (installed in the Docker image)
from azure.identity import DefaultAzureCredential  # tries managed identity, az login, env vars... in turn

from . import config

SQL_COPT_SS_ACCESS_TOKEN = 1256  # ODBC driver option id meaning "here is an Entra access token"
_credential = None                         # created lazily, reused across calls
_cached = {"token": None, "expires": 0}    # reuse a token until shortly before it expires


def _token_bytes() -> bytes:
    """Return an Entra token for Azure SQL in the format the ODBC driver wants."""
    raw = os.environ.get("SQL_ACCESS_TOKEN")  # local dev path
    if not raw:
        global _credential
        if _cached["expires"] - time.time() < 300:  # refresh 5 minutes before expiry
            _credential = _credential or DefaultAzureCredential()
            # The "scope" names the resource the token is for: Azure SQL Database
            tok = _credential.get_token("https://database.windows.net/.default")
            _cached.update(token=tok.token, expires=tok.expires_on)
        raw = _cached["token"]
    # Driver format: 4-byte little-endian length, then the token as UTF-16-LE bytes
    data = raw.encode("utf-16-le")
    return struct.pack(f"<I{len(data)}s", len(data), data)


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run one query and return rows as dicts. Always pass user values via params ('?'), never
    by building SQL strings - the driver sends them separately, so they can't change the SQL."""
    conn_str = (f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{config.SQL_SERVER},1433;"
                f"Database={config.SQL_DATABASE};"
                "Encrypt=yes;TrustServerCertificate=no;"  # TLS on, and verify the server's certificate
                "Connection Timeout=60")                   # serverless DB may need ~1 min to wake from auto-pause
    # No user/password: the token is handed to the driver before connecting
    with pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_bytes()}, timeout=60) as conn:
        conn.timeout = 15  # seconds per query: a runaway LLM-written query can't hang the agent
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]              # column names
        return [dict(zip(cols, row)) for row in cur.fetchall()]  # [{"id": 42, "price": 566563, ...}]
