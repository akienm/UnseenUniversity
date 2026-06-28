"""
test_findings.py — T-experiment-findings-log

Tests for the experiment findings log.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestMigrationEntry:
    def test_m051_exists(self):
        from unseen_university.devices.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        assert "m051_experiment_findings" in names

    def test_m051_creates_in_infra(self):
        from unseen_university.devices.igor.memory.cortex import _SCHEMA_MIGRATIONS

        for name, sql in _SCHEMA_MIGRATIONS:
            if name == "m051_experiment_findings":
                assert "infra.experiment_findings" in sql
                assert "title" in sql
                assert "result" in sql
                assert "hypothesis" in sql
                assert "evidence" in sql
                assert "tags" in sql
                break


class TestFindingsAPI:
    """Test findings.py API functions against live DB."""

    @pytest.fixture(autouse=True)
    def _ensure_table(self):
        """Create the table if the migration hasn't run yet."""
        import os

        db_url = os.environ.get("UU_HOME_DB_URL")
        if not db_url:
            pytest.skip("UU_HOME_DB_URL not set")
        import psycopg2

        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SET search_path TO infra, clan, instance, public")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS infra.experiment_findings ("
            " id SERIAL PRIMARY KEY,"
            " title TEXT NOT NULL,"
            " hypothesis TEXT,"
            " method TEXT,"
            " result TEXT NOT NULL,"
            " conclusion TEXT,"
            " participants TEXT DEFAULT '',"
            " evidence JSONB DEFAULT '[]',"
            " tags JSONB DEFAULT '[]',"
            " created_at TEXT NOT NULL,"
            " created_by TEXT NOT NULL"
            ")"
        )
        conn.commit()
        conn.close()
        yield
        # Cleanup test entries
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SET search_path TO infra, clan, instance, public")
        cur.execute("DELETE FROM experiment_findings WHERE title LIKE 'TEST_%%'")
        conn.commit()
        conn.close()

    def test_add_and_get(self):
        from devlab.claudecode.findings import add_finding, get_finding

        fid = add_finding(
            title="TEST_add_and_get",
            result="it worked",
            created_by="test",
        )
        assert fid > 0
        f = get_finding(fid)
        assert f["title"] == "TEST_add_and_get"
        assert f["result"] == "it worked"

    def test_add_with_all_fields(self):
        from devlab.claudecode.findings import add_finding, get_finding

        fid = add_finding(
            title="TEST_full_fields",
            hypothesis="X is better than Y",
            method="A/B comparison over 500 items",
            result="X won by 1 unit",
            conclusion="Use X going forward",
            participants="cc,igor,akien",
            evidence=["commit_abc123", "session_2026-04-15a"],
            tags=["reading", "model-comparison"],
            created_by="test",
        )
        f = get_finding(fid)
        assert f["hypothesis"] == "X is better than Y"
        assert f["participants"] == "cc,igor,akien"
        assert "reading" in f["tags"]

    def test_list_findings(self):
        from devlab.claudecode.findings import add_finding, list_findings

        add_finding(title="TEST_list_1", result="r1", created_by="test")
        add_finding(title="TEST_list_2", result="r2", created_by="test")
        results = list_findings(limit=100)
        titles = {f["title"] for f in results}
        assert "TEST_list_1" in titles
        assert "TEST_list_2" in titles

    def test_list_by_tag(self):
        from devlab.claudecode.findings import add_finding, list_findings

        add_finding(
            title="TEST_tagged",
            result="r",
            tags=["special-tag"],
            created_by="test",
        )
        add_finding(
            title="TEST_untagged",
            result="r",
            tags=["other"],
            created_by="test",
        )
        results = list_findings(tag="special-tag")
        titles = {f["title"] for f in results}
        assert "TEST_tagged" in titles
        assert "TEST_untagged" not in titles

    def test_search(self):
        from devlab.claudecode.findings import add_finding, search_findings

        add_finding(
            title="TEST_search_target",
            result="deepseek outperformed",
            created_by="test",
        )
        results = search_findings("deepseek")
        assert any(f["title"] == "TEST_search_target" for f in results)

    def test_get_nonexistent(self):
        from devlab.claudecode.findings import get_finding

        f = get_finding(999999)
        assert f == {}
