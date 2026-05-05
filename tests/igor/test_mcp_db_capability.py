"""T-mcp-db-query-capability: db_query + db_dispatch MCP handler tests.

Integration tests against a real Postgres DB. Requires IGOR_HOME_DB_URL.
Uses the conftest pg_test_schema fixture for isolation on writes.

Read tests: safe against live DB (SELECT 1, information_schema queries).
Write tests: use a session-specific temp table to avoid touching live data.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab" / "claudecode"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("IGOR_HOME_DB_URL"),
    reason="IGOR_HOME_DB_URL not set",
)


@pytest.fixture(scope="module")
def mcp():
    """Fresh igor_mcp module import for this test module."""
    import igor_mcp

    importlib.reload(igor_mcp)
    return igor_mcp


class TestDbQuery:
    def test_simple_select_returns_rows(self, mcp):
        result = json.loads(mcp._db_query("SELECT 1+1 AS result"))
        assert result["count"] == 1
        assert result["rows"][0]["result"] == 2

    def test_returns_count_field(self, mcp):
        result = json.loads(mcp._db_query("SELECT 1 WHERE false"))
        assert result["count"] == 0
        assert result["rows"] == []

    def test_information_schema_query(self, mcp):
        result = json.loads(
            mcp._db_query(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = 'clan' AND table_name = 'memories'"
            )
        )
        assert result["count"] >= 1
        assert result["rows"][0]["table_name"] == "memories"

    def test_parametrized_query(self, mcp):
        result = json.loads(mcp._db_query("SELECT %s::text AS val", ["hello"]))
        assert result["rows"][0]["val"] == "hello"

    def test_dispatch_routes_db_query(self, mcp):
        raw = mcp._dispatch("db_query", {"sql": "SELECT 42 AS answer"})
        data = json.loads(raw)
        assert data["rows"][0]["answer"] == 42

    def test_list_tools_includes_db_query(self, mcp):
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "db_query" in names

    def test_list_tools_includes_db_dispatch(self, mcp):
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "db_dispatch" in names


class TestDbDispatch:
    @pytest.fixture(autouse=True)
    def _temp_table(self, mcp):
        """Create a temp table for write tests, drop on teardown."""
        import psycopg2

        db_url = os.environ["IGOR_HOME_DB_URL"]
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS public._mcp_dispatch_test "
            "(id SERIAL PRIMARY KEY, val TEXT)"
        )
        cur.close()
        conn.close()
        yield
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS public._mcp_dispatch_test")
        cur.close()
        conn.close()

    def test_dispatch_insert_returns_rowcount(self, mcp):
        result = json.loads(
            mcp._db_dispatch(
                "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                ["test-value"],
            )
        )
        assert result["rowcount"] == 1
        assert "request_id" in result

    def test_dispatch_returns_request_id_uuid(self, mcp):
        import uuid

        result = json.loads(
            mcp._db_dispatch(
                "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                ["uuid-test"],
            )
        )
        uuid.UUID(result["request_id"])  # raises ValueError if not valid UUID

    def test_dispatch_update_rowcount(self, mcp):
        mcp._db_dispatch(
            "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
            ["to-update"],
        )
        result = json.loads(
            mcp._db_dispatch(
                "UPDATE public._mcp_dispatch_test SET val = %s WHERE val = %s",
                ["updated", "to-update"],
            )
        )
        assert result["rowcount"] == 1

    def test_dispatch_zero_rowcount_on_no_match(self, mcp):
        result = json.loads(
            mcp._db_dispatch(
                "DELETE FROM public._mcp_dispatch_test WHERE val = %s",
                ["__nonexistent__"],
            )
        )
        assert result["rowcount"] == 0

    def test_dispatch_routes_db_dispatch(self, mcp):
        raw = mcp._dispatch(
            "db_dispatch",
            {
                "sql": "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                "params": ["via-dispatch"],
            },
        )
        data = json.loads(raw)
        assert data["rowcount"] == 1
