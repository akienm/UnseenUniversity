"""Tests for palace_bootstrap.py — migration, seed, rollback.

Uses a randomly-named test schema so tests never touch adc.palace production data.
Requires a live Postgres at IGOR_HOME_DB_URL.
"""

from __future__ import annotations

import os
import random

import psycopg2
import psycopg2.extras
import pytest

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_SCHEMA = f"test_palace_{random.randint(10_000_000, 99_999_999)}"


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(_PG_URL)
    yield c
    # Cleanup: drop test schema
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE;")
    c.commit()
    c.close()


@pytest.fixture(scope="module", autouse=True)
def bootstrapped(conn):
    from scripts.palace_bootstrap import migrate, seed

    migrate(conn, schema=_SCHEMA)
    seed(conn, schema=_SCHEMA)


def _q(conn, sql, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


class TestMigration:
    def test_table_exists(self, conn):
        rows = _q(
            conn,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = 'palace'",
            (_SCHEMA,),
        )
        assert rows, f"table {_SCHEMA}.palace not found"

    def test_gin_index_on_metadata(self, conn):
        rows = _q(
            conn,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'palace' AND indexdef ILIKE '%%using gin%%'",
            (_SCHEMA,),
        )
        assert len(rows) >= 2, f"expected ≥2 GIN indexes, got {rows}"

    def test_required_columns_present(self, conn):
        rows = _q(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'palace'",
            (_SCHEMA,),
        )
        cols = {r["column_name"] for r in rows}
        for required in (
            "path",
            "title",
            "content",
            "node_type",
            "updated_at",
            "metadata",
        ):
            assert required in cols, f"missing column: {required}"


class TestSeededNodes:
    def _get(self, conn, path):
        rows = _q(conn, f"SELECT * FROM {_SCHEMA}.palace WHERE path = %s", (path,))
        return rows[0] if rows else None

    def test_goals_node_exists(self, conn):
        node = self._get(conn, "palace.shared.akien.goals")
        assert node is not None
        assert "Goals Tree" in node["title"]
        assert "goals_tree" in node["content"]

    def test_goals_node_has_pointer_metadata(self, conn):
        node = self._get(conn, "palace.shared.akien.goals")
        assert "pointer_to" in node["metadata"]

    def test_rules_coding_exists(self, conn):
        node = self._get(conn, "palace.shared.rules.coding")
        assert node is not None
        assert "SQLite" in node["content"] or "sqlite" in node["content"].lower()

    def test_adc_summary_exists(self, conn):
        node = self._get(conn, "palace.projects.unseen_university.summary")
        assert node is not None
        assert node["node_type"] == "doc"

    def test_adc_map_exists(self, conn):
        node = self._get(conn, "palace.projects.unseen_university.map")
        assert node is not None

    def test_adc_standards_exists(self, conn):
        node = self._get(conn, "palace.projects.unseen_university.standards")
        assert node is not None

    def test_theigors_pointer_exists(self, conn):
        node = self._get(conn, "palace.projects.theigors")
        assert node is not None
        assert node["node_type"] == "pointer"

    def test_tag_filter_query_works(self, conn):
        rows = _q(
            conn,
            f"SELECT path FROM {_SCHEMA}.palace "
            'WHERE metadata @> \'{"tags": ["shared"]}\'::jsonb',
        )
        paths = {r["path"] for r in rows}
        assert "palace.shared.rules.coding" in paths
        assert "palace.shared.akien.goals" in paths

    def test_total_node_count(self, conn):
        rows = _q(conn, f"SELECT count(*) AS n FROM {_SCHEMA}.palace")
        assert rows[0]["n"] >= 10, f"expected ≥10 seeded nodes, got {rows[0]['n']}"


class TestRollback:
    def test_rollback_drops_table(self, conn):
        from scripts.palace_bootstrap import migrate, rollback, seed

        tmp_schema = f"test_palace_rb_{random.randint(10_000_000, 99_999_999)}"
        try:
            migrate(conn, schema=tmp_schema)
            seed(conn, schema=tmp_schema)
            rollback(conn, schema=tmp_schema)
            rows = _q(
                conn,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = 'palace'",
                (tmp_schema,),
            )
            assert not rows, "table should be gone after rollback"
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {tmp_schema} CASCADE;")
            conn.commit()
