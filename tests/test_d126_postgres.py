"""
tests/test_d126_postgres.py — D126 two-channel proxy + PendingReplyStore unit tests.

Tests:
  - PGConnWrapper.executescript splits and runs multi-statement SQL
  - PGConnWrapper.execute silently no-ops PRAGMA statements
  - make_home_proxy / make_local_proxy factory routing
  - PendingReplyStore schema init, enqueue, drain
  - WordGraph initialises without error when home proxy is Postgres
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ── helpers ───────────────────────────────────────────────────────────────────


def _add_repo_to_path():
    import sys

    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()


# ── PGConnWrapper shims ───────────────────────────────────────────────────────


class TestPGConnWrapperShims(unittest.TestCase):
    """Test SQLite-compat shims on _PGConnWrapper without a real Postgres."""

    def _make_wrapper(self):
        from wild_igor.igor.memory.db_proxy import _PGConnWrapper

        # Minimal fake psycopg2 connection + cursor
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.cursor.return_value = fake_cur
        # Patch psycopg2.extras so __init__ doesn't fail
        import sys

        fake_extras = MagicMock()
        fake_extras.RealDictCursor = MagicMock()
        with patch.dict(
            sys.modules, {"psycopg2": MagicMock(), "psycopg2.extras": fake_extras}
        ):
            wrapper = _PGConnWrapper.__new__(_PGConnWrapper)
            wrapper._conn = fake_conn
            wrapper._cur = fake_cur
            wrapper._last_sql = ""
        return wrapper, fake_conn, fake_cur

    def test_pragma_is_noop(self):
        """PRAGMA statements must silently return self without touching the cursor."""
        wrapper, conn, cur = self._make_wrapper()
        result = wrapper.execute("PRAGMA journal_mode=WAL")
        self.assertIs(result, wrapper)
        cur.execute.assert_not_called()

    def test_pragma_case_insensitive(self):
        wrapper, _, cur = self._make_wrapper()
        wrapper.execute("pragma wal_checkpoint(PASSIVE)")
        cur.execute.assert_not_called()

    def test_executescript_calls_each_statement(self):
        """executescript splits on ';' and executes each non-empty statement.
        DDL SQL lands on self._cur (= fake_cur set at init).
        Savepoint housekeeping lands on self._conn.cursor() (= sp_cur set after init).
        """
        wrapper, fake_conn, fake_cur = self._make_wrapper()
        # Redirect savepoint cursor so its calls don't pollute fake_cur
        sp_cur = MagicMock()
        fake_conn.cursor.return_value = sp_cur

        wrapper.executescript("CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n")
        # DDL goes to self._cur which is fake_cur (assigned at init time)
        calls = [str(c) for c in fake_cur.execute.call_args_list]
        ddl_calls = [c for c in calls if "CREATE TABLE" in c]
        self.assertEqual(len(ddl_calls), 2)

    def test_executescript_skips_empty_statements(self):
        wrapper, fake_conn, fake_cur = self._make_wrapper()
        sp_cur = MagicMock()
        fake_conn.cursor.return_value = sp_cur

        wrapper.executescript("CREATE TABLE x (id INT);\n\n;\n")
        calls = [str(c) for c in fake_cur.execute.call_args_list]
        ddl_calls = [c for c in calls if "CREATE TABLE" in c]
        self.assertEqual(len(ddl_calls), 1)


# ── make_home_proxy / make_local_proxy routing ────────────────────────────────


class TestProxyFactories(unittest.TestCase):

    def test_make_home_proxy_uses_IGOR_HOME_DB_URL(self):
        with patch.dict(
            os.environ, {"IGOR_HOME_DB_URL": "postgresql://fake/db"}, clear=False
        ):
            with patch("wild_igor.igor.memory.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                # Force re-evaluation
                result = db_proxy.make_home_proxy()
                MockPG.assert_called_once_with(
                    "postgresql://fake/db", search_path="clan,infra,public"
                )

    def test_make_home_proxy_falls_back_to_IGOR_DB_URL(self):
        env = {"IGOR_DB_URL": "postgresql://fallback/db"}
        # Remove IGOR_HOME_DB_URL if present
        clean_env = {k: v for k, v in os.environ.items() if k != "IGOR_HOME_DB_URL"}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("wild_igor.igor.memory.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_home_proxy()
                MockPG.assert_called_once_with(
                    "postgresql://fallback/db", search_path="clan,infra,public"
                )

    def test_make_local_proxy_uses_IGOR_LOCAL_DB_URL(self):
        with patch.dict(
            os.environ, {"IGOR_LOCAL_DB_URL": "postgresql://local/db"}, clear=False
        ):
            with patch("wild_igor.igor.memory.db_proxy.PGDatabaseProxy") as MockPG:
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
        }
        clean_env["IGOR_HOME_DB_URL"] = "postgresql://test:test@localhost/test"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("wild_igor.igor.memory.db_proxy.PGDatabaseProxy") as MockPG:
                from wild_igor.igor.memory import db_proxy

                db_proxy.make_local_proxy(Path("/tmp/test.db"))
                MockPG.assert_called_once_with(
                    "postgresql://test:test@localhost/test",
                    search_path="instance,clan,infra,public",
                )


# ── PendingReplyStore ─────────────────────────────────────────────────────────


class TestPendingReplyStore(unittest.TestCase):
    """Test PendingReplyStore using real SQLite as the local proxy."""

    def _make_store(self, on_worry=None):
        from wild_igor.igor.memory.db_proxy import DatabaseProxy
        from wild_igor.igor.memory.pending_replies import PendingReplyStore

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._tmp_path = Path(tmp.name)

        local_proxy = DatabaseProxy(self._tmp_path)
        home_proxy = DatabaseProxy(self._tmp_path)  # same file for simplicity
        return PendingReplyStore(local_proxy, home_proxy, on_worry=on_worry)

    def tearDown(self):
        if hasattr(self, "_tmp_path") and self._tmp_path.exists():
            self._tmp_path.unlink()

    def test_schema_creates_table(self):
        store = self._make_store()
        conn = sqlite3.connect(str(self._tmp_path))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        self.assertIn("pending_replies", tables)

    def test_enqueue_stores_row(self):
        store = self._make_store()
        row_id = store.enqueue("wg_cooccur", "upsert", {"pairs": [["a", "b", 1.0]]})
        self.assertIsNotNone(row_id)
        count = store.pending_count()
        self.assertEqual(count, 1)

    def test_pending_count_zero_initially(self):
        store = self._make_store()
        self.assertEqual(store.pending_count(), 0)

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

    def test_word_graph_init_sqlite(self):
        """WordGraph boots cleanly on SQLite (regression guard for PendingReplyStore wiring)."""
        import tempfile
        from wild_igor.igor.cognition.word_graph import WordGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_wg.db"
            wg = WordGraph(name="test", db_path=db_path)
            self.assertIsNotNone(wg)
            self.assertIsNotNone(wg._pending)
            self.assertIsNotNone(wg._cache)
            # Basic operations work
            wg.index("doc1", "hello world test")
            results = wg.predict_next("hello world")
            self.assertIsInstance(results, list)


# ── Migration script ──────────────────────────────────────────────────────────


class TestMigrateWgScript(unittest.TestCase):
    """Smoke-test the migration script's helper functions."""

    def test_dry_run_does_not_write(self):
        """--dry-run should print counts and exit cleanly without touching Postgres."""
        import subprocess, sys

        # The migration script needs the SQLite word_graph.db to exist.
        # Skip on machines that don't have the live instance data.
        from wild_igor.igor.paths import paths as _paths

        wg_path = _paths().word_graph("word_graph")
        if not wg_path.exists():
            self.skipTest(f"word_graph.db not found at {wg_path}")

        env = os.environ.copy()
        env.setdefault(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        result = subprocess.run(
            [sys.executable, "lab/claudecode/migrate_wg_to_postgres.py", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            env=env,
        )
        self.assertEqual(result.returncode, 0, f"dry-run failed:\n{result.stderr}")
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("wg_cooccur", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
