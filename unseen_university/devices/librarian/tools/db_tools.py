"""DB tools — generic read/write SQL against the Igor Postgres DB."""

from __future__ import annotations

import json
import os
import uuid

import psycopg2.extras

from unseen_university.devices.librarian.db import get_conn

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

SCHEMAS = [
    {
        "name": "db_query",
        "description": (
            "Read-only SQL SELECT against the Igor Postgres DB. "
            "Returns rows as JSON. Use instead of `psql -tAc` shell-outs. "
            "Prefer memory_get / memory_search for single-node reads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT statement to execute"},
                "params": {
                    "type": "array",
                    "description": "Positional parameters for %s placeholders (optional)",
                    "items": {},
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "db_dispatch",
        "description": (
            "Write SQL (INSERT/UPDATE/DELETE) against the Igor Postgres DB. "
            "Returns rowcount and a request_id. "
            "Use for state changes; use db_query for reads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "INSERT, UPDATE, or DELETE statement",
                },
                "params": {
                    "type": "array",
                    "description": "Positional parameters for %s placeholders (optional)",
                    "items": {},
                },
            },
            "required": ["sql"],
        },
    },
]


def _q(sql: str, params=(), pg_url: str = _PG_URL) -> list[dict]:
    with get_conn(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def _exec(sql: str, params=(), pg_url: str = _PG_URL) -> int:
    with get_conn(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def db_query(sql: str, params: list | None = None, pg_url: str = _PG_URL) -> str:
    rows = _q(sql, params or [], pg_url)
    return json.dumps({"rows": rows, "count": len(rows)}, default=str)


def db_dispatch(sql: str, params: list | None = None, pg_url: str = _PG_URL) -> str:
    rowcount = _exec(sql, params or [], pg_url)
    return json.dumps({"rowcount": rowcount, "request_id": str(uuid.uuid4())})


def dispatch(name: str, args: dict, pg_url: str = _PG_URL) -> str | None:
    if name == "db_query":
        return db_query(args["sql"], args.get("params"), pg_url)
    if name == "db_dispatch":
        return db_dispatch(args["sql"], args.get("params"), pg_url)
    return None
