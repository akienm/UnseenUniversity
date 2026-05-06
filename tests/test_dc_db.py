"""
test_dc_db.py — Smoke test for make_dc_proxy() and agent-datacenter-0001 connectivity.

Requires a live Postgres instance with the agent-datacenter-0001 database.
Set AGENT_DATACENTER_DB_URL before running (or uses the default shown below).
Skipped automatically when the DB is unreachable.
"""

import os

import pytest

# Default for local dev — override via env for CI
os.environ.setdefault(
    "AGENT_DATACENTER_DB_URL",
    "postgresql://datacenter:choose_a_password@127.0.0.1/agent-datacenter-0001",
)


def _db_reachable() -> bool:
    try:
        import psycopg2

        url = os.environ.get("AGENT_DATACENTER_DB_URL", "")
        conn = psycopg2.connect(url, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="agent-datacenter-0001 not reachable — set AGENT_DATACENTER_DB_URL",
)


def test_make_dc_proxy_returns_proxy():
    from agent_datacenter.db import make_dc_proxy, PGDatabaseProxy

    proxy = make_dc_proxy()
    assert isinstance(proxy, PGDatabaseProxy)


def test_dc_proxy_connects():
    from agent_datacenter.db import make_dc_proxy

    with make_dc_proxy()() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM memory_palace").fetchone()
        assert rows[0] >= 0


def test_dc_proxy_memories_table_exists():
    from agent_datacenter.db import make_dc_proxy

    with make_dc_proxy()() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        assert rows[0] >= 0


def test_make_dc_proxy_raises_without_url():
    from agent_datacenter.db import make_dc_proxy

    original = os.environ.pop("AGENT_DATACENTER_DB_URL", None)
    original_alt = os.environ.pop("AGENT_DATACENTER_POSTGRES_URL", None)
    try:
        with pytest.raises(RuntimeError, match="AGENT_DATACENTER_DB_URL not set"):
            make_dc_proxy()
    finally:
        if original is not None:
            os.environ["AGENT_DATACENTER_DB_URL"] = original
        if original_alt is not None:
            os.environ["AGENT_DATACENTER_POSTGRES_URL"] = original_alt
