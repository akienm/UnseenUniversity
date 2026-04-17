"""
test_three_schemas.py — T-uc-schema-three-namespaces

Tests for the three-schema Postgres migration: instance, clan, infra.
Verifies search_path handling in PGDatabaseProxy and migration entries.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestPGDatabaseProxySearchPath:
    """PGDatabaseProxy accepts and stores search_path."""

    def test_default_search_path(self):
        with patch("psycopg2.pool.ThreadedConnectionPool"):
            from wild_igor.igor.memory.db_proxy import PGDatabaseProxy

            proxy = PGDatabaseProxy("postgresql://fake", search_path=None)
            assert proxy._search_path == "instance,clan,infra,public"

    def test_custom_search_path(self):
        with patch("psycopg2.pool.ThreadedConnectionPool"):
            from wild_igor.igor.memory.db_proxy import PGDatabaseProxy

            proxy = PGDatabaseProxy(
                "postgresql://fake", search_path="clan,infra,public"
            )
            assert proxy._search_path == "clan,infra,public"

    def test_infra_only_search_path(self):
        with patch("psycopg2.pool.ThreadedConnectionPool"):
            from wild_igor.igor.memory.db_proxy import PGDatabaseProxy

            proxy = PGDatabaseProxy("postgresql://fake", search_path="infra,public")
            assert proxy._search_path == "infra,public"


class TestFactorySearchPaths:
    """Factory functions pass correct search_path to PGDatabaseProxy."""

    @patch.dict("os.environ", {"IGOR_HOME_DB_URL": "postgresql://fake"})
    @patch("psycopg2.pool.ThreadedConnectionPool")
    def test_home_proxy_excludes_instance(self, mock_pool):
        from wild_igor.igor.memory.db_proxy import make_home_proxy

        proxy = make_home_proxy()
        assert proxy._search_path == "clan,infra,public"

    @patch.dict("os.environ", {"IGOR_HOME_DB_URL": "postgresql://fake"})
    @patch("psycopg2.pool.ThreadedConnectionPool")
    def test_local_proxy_includes_all(self, mock_pool):
        from wild_igor.igor.memory.db_proxy import make_local_proxy

        proxy = make_local_proxy()
        assert proxy._search_path == "instance,clan,infra,public"

    @patch.dict("os.environ", {"IGOR_HOME_DB_URL": "postgresql://fake"})
    @patch("psycopg2.pool.ThreadedConnectionPool")
    def test_infra_proxy_infra_only(self, mock_pool):
        from wild_igor.igor.memory.db_proxy import make_infra_proxy

        proxy = make_infra_proxy()
        assert proxy._search_path == "infra,public"

    @patch.dict("os.environ", {}, clear=False)
    def test_infra_proxy_returns_none_without_url(self):
        import os

        os.environ.pop("IGOR_HOME_DB_URL", None)
        os.environ.pop("IGOR_DB_URL", None)
        from wild_igor.igor.memory.db_proxy import make_infra_proxy

        result = make_infra_proxy()
        assert result is None


class TestSearchPathOnConnect:
    """search_path is SET on every connection checkout."""

    @patch("psycopg2.pool.ThreadedConnectionPool")
    def test_context_sets_search_path(self, mock_pool_cls):
        from wild_igor.igor.memory.db_proxy import PGDatabaseProxy

        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_conn = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        proxy = PGDatabaseProxy("postgresql://fake", search_path="clan,infra,public")
        ctx = proxy()
        with ctx as wrapper:
            # Verify SET search_path was called on the cursor
            calls = [str(c) for c in mock_cursor.execute.call_args_list]
            assert any("SET search_path TO clan,infra,public" in c for c in calls)


class TestMigrationEntries:
    """Migration list contains all three-schema entries."""

    def test_schema_creation_migrations_exist(self):
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        assert "m050_schema_instance" in names
        assert "m050_schema_clan" in names
        assert "m050_schema_infra" in names

    def test_clan_table_migrations_exist(self):
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        clan_tables = [
            "memories",
            "memory_embeddings",
            "memory_blobs",
            "interpretive_edges",
            "reading_list",
            "reading_runs",
            "reading_run_items",
            "lists",
            "traces",
            "experiment_queue",
            "trees",
            "node_registry",
        ]
        for table in clan_tables:
            assert f"m050_clan_{table}" in names, f"Missing clan migration for {table}"

    def test_instance_table_migrations_exist(self):
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        instance_tables = [
            "ring_memory",
            "twm_observations",
            "tails",
            "traversal_contexts",
            "pending_replies",
            "wg_access_log",
            "cloud_escalations",
        ]
        for table in instance_tables:
            assert (
                f"m050_instance_{table}" in names
            ), f"Missing instance migration for {table}"

    def test_infra_table_migrations_exist(self):
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        infra_tables = [
            "machines",
            "channel_messages",
            "sessions",
            "slates",
            "memory_palace",
            "instance_log",
            "decisions",
            "docs_entries",
            "github_tickets",
        ]
        for table in infra_tables:
            assert (
                f"m050_infra_{table}" in names
            ), f"Missing infra migration for {table}"

    def test_migrations_table_stays_in_public(self):
        """_migrations must stay in public — it's the bootstrap table."""
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        assert "m050_infra__migrations" not in names
        assert "m050_clan__migrations" not in names

    def test_schema_creation_before_moves(self):
        """Schema creation must come before ALTER TABLE SET SCHEMA."""
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = [name for name, _ in _SCHEMA_MIGRATIONS]
        schema_idx = max(
            names.index("m050_schema_instance"),
            names.index("m050_schema_clan"),
            names.index("m050_schema_infra"),
        )
        # All move migrations must come after schema creation
        for i, (name, _) in enumerate(_SCHEMA_MIGRATIONS):
            if name.startswith("m050_") and "schema_" not in name:
                assert (
                    i > schema_idx
                ), f"{name} at index {i} is before schema creation at {schema_idx}"

    def test_memories_before_fk_dependents(self):
        """memories must move before tables that reference it via FK."""
        from wild_igor.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = [name for name, _ in _SCHEMA_MIGRATIONS]
        mem_idx = names.index("m050_clan_memories")
        for dep in [
            "m050_clan_memory_embeddings",
            "m050_clan_memory_blobs",
            "m050_clan_interpretive_edges",
        ]:
            dep_idx = names.index(dep)
            assert (
                dep_idx > mem_idx
            ), f"{dep} at index {dep_idx} must come after memories at {mem_idx}"


class TestPGSchemaBootstrap:
    """_PG_SCHEMA creates schemas on fresh DB."""

    def test_pg_schema_creates_schemas(self):
        from wild_igor.igor.memory.cortex import _PG_SCHEMA

        assert "CREATE SCHEMA IF NOT EXISTS instance" in _PG_SCHEMA
        assert "CREATE SCHEMA IF NOT EXISTS clan" in _PG_SCHEMA
        assert "CREATE SCHEMA IF NOT EXISTS infra" in _PG_SCHEMA

    def test_pg_schema_creates_schemas_before_tables(self):
        from wild_igor.igor.memory.cortex import _PG_SCHEMA

        schema_pos = _PG_SCHEMA.index("CREATE SCHEMA IF NOT EXISTS instance")
        table_pos = _PG_SCHEMA.index("CREATE TABLE IF NOT EXISTS _migrations")
        assert schema_pos < table_pos
