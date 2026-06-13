"""T-migrations-lookup-cache: per-process cache of _migrations table lookups.

Before this fix, SELECT 1 FROM _migrations WHERE name = 'scope_backfill_123'
fired on every Cortex() instantiation (9k+ hits/week). After: one
SELECT name FROM _migrations per process per DB, then O(1) set-membership
checks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.memory import cortex as cortex_mod


@pytest.fixture(autouse=True)
def _clean_cache():
    cortex_mod._MIGRATION_CACHE.clear()
    yield
    cortex_mod._MIGRATION_CACHE.clear()


def _mock_conn_with_migrations(names):
    """Build a mock conn whose SELECT name FROM _migrations returns the given names."""
    conn = MagicMock()
    rows = [{"name": n} for n in names]
    select_result = MagicMock()
    select_result.fetchall.return_value = rows
    conn.execute.return_value = select_result
    return conn


def test_first_lookup_hits_db():
    conn = _mock_conn_with_migrations(["scope_backfill_123"])
    result = cortex_mod._applied_migrations(conn, "/tmp/foo.db")
    assert result == {"scope_backfill_123"}
    conn.execute.assert_called_once_with("SELECT name FROM _migrations")


def test_second_lookup_skips_db():
    conn = _mock_conn_with_migrations(["scope_backfill_123"])
    cortex_mod._applied_migrations(conn, "/tmp/foo.db")
    conn.reset_mock()

    result = cortex_mod._applied_migrations(conn, "/tmp/foo.db")

    assert result == {"scope_backfill_123"}
    conn.execute.assert_not_called()


def test_different_dbs_have_separate_caches():
    conn_a = _mock_conn_with_migrations(["m_a"])
    conn_b = _mock_conn_with_migrations(["m_b"])

    cache_a = cortex_mod._applied_migrations(conn_a, "/tmp/a.db")
    cache_b = cortex_mod._applied_migrations(conn_b, "/tmp/b.db")

    assert cache_a == {"m_a"}
    assert cache_b == {"m_b"}


def test_cache_mutation_persists_across_lookups():
    """When a caller .add()s to the returned set, the cache is updated too."""
    conn = _mock_conn_with_migrations([])
    applied = cortex_mod._applied_migrations(conn, "/tmp/x.db")
    applied.add("new_migration")

    conn2 = _mock_conn_with_migrations(["should_not_read_this"])
    result = cortex_mod._applied_migrations(conn2, "/tmp/x.db")

    assert "new_migration" in result
    assert "should_not_read_this" not in result


def test_missing_migrations_table_returns_empty():
    """Fresh DB where _migrations doesn't exist yet must not raise."""
    conn = MagicMock()
    conn.execute.side_effect = Exception("relation _migrations does not exist")

    result = cortex_mod._applied_migrations(conn, "/tmp/fresh.db")

    assert result == set()


def test_live_postgres_cache_populates(monkeypatch):
    """Integration: on the real Postgres DB, the cache picks up scope_backfill_123."""
    import os

    if not os.environ.get("UU_HOME_DB_URL"):
        pytest.skip("UU_HOME_DB_URL not set — skipping live DB check")

    import psycopg2

    conn_raw = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:

        class _ConnShim:
            def execute(self, sql, params=()):
                cur = conn_raw.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()

                class _Result:
                    def fetchall(self_inner):
                        return [{"name": r[0]} for r in rows]

                return _Result()

        applied = cortex_mod._applied_migrations(_ConnShim(), "live-test-key")
        assert (
            "scope_backfill_123" in applied
        ), "live _migrations should contain scope_backfill_123"
    finally:
        conn_raw.close()
