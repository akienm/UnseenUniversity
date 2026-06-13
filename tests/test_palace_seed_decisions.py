"""Tests for scripts/palace_seed_decisions.py — parse + seed palace.decisions.*

Uses a randomly-named test schema; never touches adc.palace production data.
Requires UU_HOME_DB_URL.
"""

from __future__ import annotations

import os
import random
import textwrap
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_SCHEMA = f"test_decisions_{random.randint(10_000_000, 99_999_999)}"


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(_PG_URL)
    yield c
    with c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE;")
    c.commit()
    c.close()


@pytest.fixture(scope="module", autouse=True)
def schema_ready(conn):
    """Create the palace table in the test schema before any test runs."""
    from scripts.palace_bootstrap import migrate

    migrate(conn, schema=_SCHEMA)


def _q(conn, sql, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ── _parse_md ─────────────────────────────────────────────────────────────────


class TestParsemd:
    def test_extracts_title(self):
        from scripts.palace_seed_decisions import _parse_md

        text = "# D-foo\n**title:** My Decision\n**date:** 2026-05-01\n"
        result = _parse_md(text)
        assert result["title"] == "My Decision"

    def test_extracts_date(self):
        from scripts.palace_seed_decisions import _parse_md

        text = "**date:** 2026-04-27\n"
        result = _parse_md(text)
        assert result["date"] == "2026-04-27"

    def test_extracts_status(self):
        from scripts.palace_seed_decisions import _parse_md

        text = "**status:** closed\n"
        result = _parse_md(text)
        assert result["status"] == "closed"

    def test_extracts_spawned_tickets(self):
        from scripts.palace_seed_decisions import _parse_md

        text = "**spawned_tickets:** T-foo, T-bar, T-baz\n"
        result = _parse_md(text)
        assert result["spawned_tickets"] == ["T-foo", "T-bar", "T-baz"]

    def test_defaults_on_missing_fields(self):
        from scripts.palace_seed_decisions import _parse_md

        result = _parse_md("just some content")
        assert result["status"] == "open"
        assert result["spawned_tickets"] == []


# ── load_decisions ────────────────────────────────────────────────────────────


class TestLoadDecisions:
    def test_reads_md_files(self, tmp_path):
        from scripts.palace_seed_decisions import load_decisions

        (tmp_path / "D-test-decision-2026-05-01.md").write_text(
            "**title:** Test Decision\n**date:** 2026-05-01\n**status:** open\n\n## body\ncontent here\n"
        )
        nodes = load_decisions(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["path"] == "palace.decisions.D-test-decision-2026-05-01"
        assert nodes[0]["title"] == "Test Decision"

    def test_non_decision_files_ignored(self, tmp_path):
        from scripts.palace_seed_decisions import load_decisions

        (tmp_path / "README.md").write_text("not a decision")
        (tmp_path / "D-real-2026-05-01.md").write_text("**title:** Real\n")
        nodes = load_decisions(tmp_path)
        assert len(nodes) == 1

    def test_node_type_is_decision(self, tmp_path):
        from scripts.palace_seed_decisions import load_decisions

        (tmp_path / "D-x-2026-05-01.md").write_text("**title:** X\n")
        nodes = load_decisions(tmp_path)
        assert nodes[0]["node_type"] == "decision"

    def test_tags_include_decision(self, tmp_path):
        from scripts.palace_seed_decisions import load_decisions
        import json

        (tmp_path / "D-adc-thing-2026-05-01.md").write_text("**title:** T\n")
        nodes = load_decisions(tmp_path)
        meta = nodes[0]["metadata"].adapted  # psycopg2.extras.Json wraps the dict
        assert "decision" in meta["tags"]
        assert "adc" in meta["tags"]


# ── seed (integration) ────────────────────────────────────────────────────────


class TestSeedIntegration:
    def test_seed_writes_nodes(self, conn, tmp_path):
        from scripts.palace_seed_decisions import load_decisions, seed

        (tmp_path / "D-seed-test-2026-05-01.md").write_text(
            "**title:** Seed Test\n**date:** 2026-05-01\n**status:** open\n\ncontent\n"
        )
        nodes = load_decisions(tmp_path)
        count = seed(conn, nodes, schema=_SCHEMA)
        assert count == 1

    def test_seeded_node_queryable(self, conn, tmp_path):
        from scripts.palace_seed_decisions import load_decisions, seed

        (tmp_path / "D-query-test-2026-05-01.md").write_text(
            "**title:** Query Test\n**date:** 2026-05-01\n\ncontent\n"
        )
        nodes = load_decisions(tmp_path)
        seed(conn, nodes, schema=_SCHEMA)
        rows = _q(
            conn,
            f"SELECT path, title FROM {_SCHEMA}.palace WHERE path = %s",
            ("palace.decisions.D-query-test-2026-05-01",),
        )
        assert rows, "node not found"
        assert rows[0]["title"] == "Query Test"

    def test_upsert_is_idempotent(self, conn, tmp_path):
        from scripts.palace_seed_decisions import load_decisions, seed

        md = tmp_path / "D-idem-2026-05-01.md"
        md.write_text("**title:** Idem\n**date:** 2026-05-01\n\ncontent\n")
        nodes = load_decisions(tmp_path)
        seed(conn, nodes, schema=_SCHEMA)
        seed(conn, nodes, schema=_SCHEMA)
        rows = _q(
            conn,
            f"SELECT count(*) AS n FROM {_SCHEMA}.palace WHERE path = %s",
            ("palace.decisions.D-idem-2026-05-01",),
        )
        assert rows[0]["n"] == 1

    def test_tag_filter_works(self, conn, tmp_path):
        from scripts.palace_seed_decisions import load_decisions, seed

        (tmp_path / "D-adc-tag-test-2026-05-01.md").write_text(
            "**title:** Tag Test\n**date:** 2026-05-01\n\ncontent\n"
        )
        nodes = load_decisions(tmp_path)
        seed(conn, nodes, schema=_SCHEMA)
        rows = _q(
            conn,
            f'SELECT path FROM {_SCHEMA}.palace WHERE metadata @> \'{{"tags":["decision"]}}\'::jsonb',
        )
        paths = {r["path"] for r in rows}
        assert "palace.decisions.D-adc-tag-test-2026-05-01" in paths
