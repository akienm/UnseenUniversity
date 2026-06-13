"""Librarian connection pool — psycopg2 ThreadedConnectionPool for sync MCP path.

Librarian hits Postgres directly (no extra hop). Pool is module-level so
connections are reused across tool calls within the same server process.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extensions
import psycopg2.pool

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_POOL_MIN = 2
_POOL_MAX = 10
_CHECKOUT_TIMEOUT_S = 5.0
_CHECKOUT_RETRY_INTERVAL_S = 0.05

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool(pg_url: str = _PG_URL) -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=_POOL_MIN,
            maxconn=_POOL_MAX,
            dsn=pg_url,
        )
    return _pool


def reset_pool() -> None:
    """Close and discard the module-level pool. Used in tests."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
    _pool = None


@contextmanager
def get_conn(
    pg_url: str = _PG_URL,
) -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager: check out a pooled connection, return it on exit."""
    pool = _get_pool(pg_url)
    deadline = time.monotonic() + _CHECKOUT_TIMEOUT_S
    conn = None
    while conn is None:
        try:
            conn = pool.getconn()
        except psycopg2.pool.PoolError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not obtain DB connection within {_CHECKOUT_TIMEOUT_S}s "
                    f"(pool max={_POOL_MAX})"
                )
            time.sleep(_CHECKOUT_RETRY_INTERVAL_S)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
