"""Azure SQL access with Entra ID tokens - no password.

Token source, in order:
  1. SQL_ACCESS_TOKEN env var (local dev: run_agent.sh passes a short-lived token from `az`)
  2. DefaultAzureCredential (in Azure: the Container App's managed identity)
"""
import os
import struct
import time

import pyodbc
from azure.identity import DefaultAzureCredential

from . import config

SQL_COPT_SS_ACCESS_TOKEN = 1256
_credential = None
_cached = {"token": None, "expires": 0}


def _token_bytes() -> bytes:
    raw = os.environ.get("SQL_ACCESS_TOKEN")
    if not raw:
        global _credential
        if _cached["expires"] - time.time() < 300:  # refresh 5 minutes before expiry
            _credential = _credential or DefaultAzureCredential()
            tok = _credential.get_token("https://database.windows.net/.default")
            _cached.update(token=tok.token, expires=tok.expires_on)
        raw = _cached["token"]
    data = raw.encode("utf-16-le")
    return struct.pack(f"<I{len(data)}s", len(data), data)


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn_str = (f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{config.SQL_SERVER},1433;"
                f"Database={config.SQL_DATABASE};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60")
    with pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_bytes()}, timeout=60) as conn:
        conn.timeout = 15  # seconds per query: a runaway LLM-written query can't hang the agent
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
