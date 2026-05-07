"""
tests/test_d126_postgres.py — D126 two-channel proxy + PendingReplyStore unit tests.

Tests:
  - make_home_proxy / make_local_proxy factory routing
  - PendingReplyStore schema init, enqueue, drain
  - WordGraph initialises without error when home proxy is Postgres
"""

from __future__ import annotations

import os
import pytest
import unittest
from pathlib import Path
from unittest.mock import patch

# ── helpers ───────────────────────────────────────────────────────────────────


def _add_repo_to_path():
    import sys

    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()

# Pre-import before any patch blocks so PGDatabaseProxy is bound to the real
# class in the shim's namespace; patches replace it in agent_datacenter.db_proxy
# only, and the binding in the shim is never the Mock.
from wild_igor.igor.memory import db_proxy as _db_proxy_module  # noqa: E402

# ── make_home_proxy / make_local_proxy routing ────────────────────────────────


class TestProxyFactories(unittest.TestCase):
    # Clear test-schema overrides from the pg_test_schema session fixture
    # so these tests exercise production defaults, not session-scoped paths.
    _SEARCH_OVERRIDES = frozenset({"IGOR_HOME_SEARCH_PATH", "IGOR_LOCAL_SEARCH_PATH"})

    def test_make_home_proxy_uses_IGOR_HOME_DB_URL(self):
        env = {k: v for k, v in os.environ.items() if k not in self._SEARCH_OVERRIDES}
        env["IGOR_HOME_DB_URL"] = "postgresql://fake/db"
        with patch.dict(os.environ, env, clear=True):
            with patch("agent_datacenter.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_home_proxy()
                MockPG.assert_called_once_with(
                    "postgresql://fake/db", search_path="clan,infra,public"
                )

    def test_make_home_proxy_falls_back_to_IGOR_DB_URL(self):
        env = {"IGOR_DB_URL": "postgresql://fallback/db"}
        # Remove IGOR_HOME_DB_URL + test-schema overrides so the test
        # exercises the production defaults, not the session-fixture paths.
        _EXCLUDE = {
            "IGOR_HOME_DB_URL",
            "IGOR_HOME_SEARCH_PATH",
            "IGOR_LOCAL_SEARCH_PATH",
        }
        clean_env = {k: v for k, v in os.environ.items() if k not in _EXCLUDE}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("agent_datacenter.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_home_proxy()
                MockPG.assert_called_once_with(
                    "postgresql://fallback/db", search_path="clan,infra,public"
                )

    def test_make_local_proxy_uses_IGOR_LOCAL_DB_URL(self):
        env = {k: v for k, v in os.environ.items() if k not in self._SEARCH_OVERRIDES}
        env["IGOR_LOCAL_DB_URL"] = "postgresql://local/db"
        with patch.dict(os.environ, env, clear=True):
            with patch("agent_datacenter.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_local_proxy()
                MockPG.assert_called_once_with(
                    "postgresql://local/db", search_path="instance,clan,infra,public"
                )

    def test_make_local_proxy_uses_home_db_when_no_local(self):
        """Local proxy falls back to IGOR_HOME_DB_URL when IGOR_LOCAL_DB_URL is unset."""
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("IGOR_LOCAL_DB_URL", "IGOR_HOME_DB_URL", "IGOR_DB_URL")
            and k not in self._SEARCH_OVERRIDES
        }
        clean_env["IGOR_HOME_DB_URL"] = "postgresql://test:test@localhost/test"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("agent_datacenter.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_local_proxy(Path("/tmp/test.db"))
                MockPG.assert_called_once_with(
                    "postgresql://test:test@localhost/test",
                    search_path="instance,clan,infra,public",
                )


# ── PendingReplyStore ─────────────────────────────────────────────────────────


class TestPendingReplyStore(unittest.TestCase):
    """Test PendingReplyStore using Postgres (make_local_proxy / make_home_proxy)."""

    def _make_store(self, on_worry=None):
        from wild_igor.igor.memory.db_proxy import make_home_proxy, make_local_proxy
        from wild_igor.igor.memory.pending_replies import PendingReplyStore

        local_proxy = make_local_proxy()
        home_proxy = make_home_proxy()
        return PendingReplyStore(local_proxy, home_proxy, on_worry=on_worry)

    def setUp(self):
        # Clear test rows so each test starts with a known baseline
        from wild_igor.igor.memory.db_proxy import make_local_proxy

        try:
            with make_local_proxy()() as conn:
                conn.execute(
                    "DELETE FROM pending_replies WHERE table_name = %s",
                    ("wg_cooccur_test",),
                )
        except Exception:
            pass

    def test_schema_creates_table(self):
        """pending_replies table is accessible via the local proxy."""
        store = self._make_store()
        # If _ensure_schema succeeds and pending_count() works, table exists
        count = store.pending_count()
        self.assertIsInstance(count, int)

    def test_enqueue_stores_row(self):
        store = self._make_store()
        before = store.pending_count()
        row_id = store.enqueue(
            "wg_cooccur_test", "upsert", {"pairs": [["a", "b", 1.0]]}
        )
        self.assertIsNotNone(row_id)
        self.assertIsInstance(row_id, int)
        count = store.pending_count()
        self.assertEqual(count, before + 1)

    def test_pending_count_is_integer(self):
        store = self._make_store()
        count = store.pending_count()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    def test_worry_fires_via_callback(self):
        """_raise_worry calls the on_worry callback with the reason string."""
        worried = []
        store = self._make_store(on_worry=lambda msg: worried.append(msg))
        store._raise_worry("test: home DB unreachable after 3 attempts")
        self.assertEqual(len(worried), 1)
        self.assertIn("test:", worried[0])


# ── WordGraph Postgres compat ─────────────────────────────────────────────────


class TestWordGraphPostgresCompat(unittest.TestCase):
    """WordGraph must initialise against a real SQLite proxy without error.
    (Full Postgres test requires a running Postgres — skipped in unit test context.)
    """

    def test_word_graph_init_postgres(self):
        """WordGraph boots cleanly on Postgres (regression guard for PendingReplyStore wiring).

        Post T-sqlite-out-word-graph-db: WordGraph is Postgres-only. No db_path.
        """
        from wild_igor.igor.cognition.word_graph import WordGraph

        wg = WordGraph(name="test")
        self.assertIsNotNone(wg)
        self.assertIsNotNone(wg._pending)
        self.assertIsNotNone(wg._cache)


# Migration-script test removed in T-sqlite-out-word-graph-db: SQLite
# word_graph.db deleted, paths().word_graph() removed. The migration script
# (lab/claudecode/migrate_wg_to_postgres.py) is kept as historical artifact.


if __name__ == "__main__":
    unittest.main(verbosity=2)
