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
    from agent_datacenter.db_proxy import make_dc_proxy, PGDatabaseProxy

    proxy = make_dc_proxy()
    assert isinstance(proxy, PGDatabaseProxy)


def test_dc_proxy_connects():
    from agent_datacenter.db_proxy import make_dc_proxy

    with make_dc_proxy()() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM memory_palace").fetchone()
        assert rows[0] >= 0


def test_dc_proxy_memories_table_exists():
    from agent_datacenter.db_proxy import make_dc_proxy

    with make_dc_proxy()() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        assert rows[0] >= 0


def test_slow_query_writes_to_slow_queries_log(tmp_path):
    """slow_queries.log is created and written on first slow query."""
    import agent_datacenter.db_proxy as db_mod

    # Reset handler state so the lazy-init fires fresh in this test
    original = db_mod._slow_handler_installed
    # Remove any previously-added handlers pointing to a real path
    old_handlers = list(db_mod.log.handlers)
    for h in old_handlers:
        db_mod.log.removeHandler(h)
    db_mod._slow_handler_installed = False

    old_env = os.environ.get("IGOR_LOG_DIR")
    os.environ["IGOR_LOG_DIR"] = str(tmp_path)
    try:
        proxy = db_mod.PGDatabaseProxy.__new__(db_mod.PGDatabaseProxy)
        proxy._latencies = __import__("collections").deque(maxlen=500)
        proxy._errors = 0
        proxy._slow = 0
        proxy._calls = 0
        proxy._connect_errors = 0

        # Trigger a "slow" query by passing elapsed_ms > threshold
        proxy._record(
            elapsed_ms=999, error=False, last_sql="SELECT slow_test FROM nowhere"
        )

        slow_log = tmp_path / "slow_queries.log"
        assert slow_log.exists(), "slow_queries.log was not created"
        content = slow_log.read_text(encoding="utf-8")
        assert "slow query" in content
        assert "999" in content
    finally:
        os.environ.pop("IGOR_LOG_DIR", None)
        if old_env is not None:
            os.environ["IGOR_LOG_DIR"] = old_env
        # Restore handler state
        for h in list(db_mod.log.handlers):
            db_mod.log.removeHandler(h)
        for h in old_handlers:
            db_mod.log.addHandler(h)
        db_mod._slow_handler_installed = original


def test_make_dc_proxy_raises_without_url():
    from agent_datacenter.db_proxy import make_dc_proxy

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
