"""
db.py — Minimal Postgres proxy for agent-datacenter-0001.

Self-contained: no TheIgors imports, no SQLite fallback, no IgorBase.
Callers use:

    proxy = make_dc_proxy()
    with proxy() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM memory_palace").fetchone()

make_dc_proxy() reads AGENT_DATACENTER_DB_URL from the environment.
AGENT_DATACENTER_POSTGRES_URL is accepted as a back-compat alias.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal connection wrapper
# ---------------------------------------------------------------------------


class _PGConnWrapper:
    """
    Thin wrapper around a psycopg2 connection that exposes a sqlite3-like interface:
      - execute(sql, params) → self (chainable for .fetchone()/.fetchall())
      - fetchone() / fetchall() / commit() / rollback()
    """

    __slots__ = ("_conn", "_cur", "_last_sql")

    def __init__(self, conn) -> None:
        import psycopg2.extras

        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        self._last_sql: str = ""

    def execute(self, sql: str, params=()) -> "_PGConnWrapper":
        self._last_sql = sql
        self._cur.execute(sql, params or ())
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _PGRowProxy(row)

    def fetchall(self):
        return [_PGRowProxy(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount if self._cur.rowcount >= 0 else 0

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        try:
            self._cur.close()
        except Exception as exc:
            log.warning("close cursor error: %s", exc)
        try:
            self._conn.close()
        except Exception as exc:
            log.warning("close conn error: %s", exc)


class _PGRowProxy:
    """
    Makes psycopg2 RealDictRow support both row["col"] and row[0] (integer index) access,
    matching sqlite3.Row behaviour expected by callers.
    """

    __slots__ = ("_d", "_keys")

    def __init__(self, row) -> None:
        self._d = dict(row)
        self._keys = list(self._d.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._d[self._keys[key]]
        return self._d[key]

    def __iter__(self):
        return iter(self._d.values())

    def keys(self):
        return self._keys

    def get(self, key, default=None):
        return self._d.get(key, default)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class _PGContext:
    """Context manager yielded by PGDatabaseProxy() — opens a connection, commits/rolls back on exit."""

    __slots__ = ("_proxy", "_wrapper")

    def __init__(self, proxy: "PGDatabaseProxy") -> None:
        self._proxy = proxy
        self._wrapper: Optional[_PGConnWrapper] = None

    def __enter__(self) -> _PGConnWrapper:
        import psycopg2

        conn = psycopg2.connect(self._proxy.db_url, connect_timeout=5)
        self._wrapper = _PGConnWrapper(conn)
        return self._wrapper

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._wrapper is not None:
            try:
                if exc_type is None:
                    self._wrapper.commit()
                else:
                    self._wrapper.rollback()
            except Exception as exc:
                log.warning("transaction finalise error: %s", exc)
            try:
                self._wrapper.close()
            except Exception as exc:
                log.warning("connection close error: %s", exc)
        return False  # never suppress exceptions


# ---------------------------------------------------------------------------
# Proxy class
# ---------------------------------------------------------------------------


class PGDatabaseProxy:
    """
    Minimal Postgres proxy for agent-datacenter-0001.

    Usage:
        proxy = PGDatabaseProxy(db_url)
        with proxy() as conn:
            conn.execute("SELECT ...")

    No connection pooling — opens a new connection per call.  Suitable for
    low-concurrency datacenter services; add a pool if throughput demands it.
    """

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url

    def __call__(self) -> _PGContext:
        return _PGContext(self)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_dc_proxy() -> PGDatabaseProxy:
    """
    Return a PGDatabaseProxy for agent-datacenter-0001.

    Reads AGENT_DATACENTER_DB_URL from the environment.
    AGENT_DATACENTER_POSTGRES_URL is accepted as a back-compat alias.

    Raises RuntimeError if neither variable is set or neither is a Postgres URL.
    """
    url = os.environ.get("AGENT_DATACENTER_DB_URL") or os.environ.get(
        "AGENT_DATACENTER_POSTGRES_URL"
    )
    if not url:
        raise RuntimeError(
            "AGENT_DATACENTER_DB_URL not set — "
            "export AGENT_DATACENTER_DB_URL=postgresql://datacenter:...@host/agent-datacenter-0001"
        )
    if not url.startswith("postgresql"):
        raise RuntimeError(
            f"AGENT_DATACENTER_DB_URL must be a postgresql:// URL, got: {url!r}"
        )
    return PGDatabaseProxy(url)
