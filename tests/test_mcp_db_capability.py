"""T-mcp-db-query-capability: db_query + db_dispatch MCP handler tests.

Integration tests against a real Postgres DB. Requires IGOR_HOME_DB_URL.

Updated for T-igor-mcp-delete: imports from Librarian db_tools instead of igor_mcp.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "dev"
        / "src"
        / "unseen_university"
    ),
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("IGOR_HOME_DB_URL"),
    reason="IGOR_HOME_DB_URL not set",
)

from unseen_university.devices.librarian.tools.db_tools import (  # noqa: E402
    db_dispatch,
    db_query,
    dispatch,
)
from unseen_university.devices.librarian.tools import SCHEMAS  # noqa: E402


class TestDbQuery:
    def test_simple_select_returns_rows(self):
        result = json.loads(db_query("SELECT 1+1 AS result"))
        assert result["count"] == 1
        assert result["rows"][0]["result"] == 2

    def test_returns_count_field(self):
        result = json.loads(db_query("SELECT 1 WHERE false"))
        assert result["count"] == 0
        assert result["rows"] == []

    def test_information_schema_query(self):
        result = json.loads(
            db_query(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = 'clan' AND table_name = 'memories'"
            )
        )
        assert result["count"] >= 1
        assert result["rows"][0]["table_name"] == "memories"

    def test_parametrized_query(self):
        result = json.loads(db_query("SELECT %s::text AS val", ["hello"]))
        assert result["rows"][0]["val"] == "hello"

    def test_dispatch_routes_db_query(self):
        raw = dispatch("db_query", {"sql": "SELECT 42 AS answer"})
        data = json.loads(raw)
        assert data["rows"][0]["answer"] == 42

    def test_schemas_includes_db_query(self):
        names = {s["name"] for s in SCHEMAS}
        assert "db_query" in names

    def test_schemas_includes_db_dispatch(self):
        names = {s["name"] for s in SCHEMAS}
        assert "db_dispatch" in names


class TestDbDispatch:
    @pytest.fixture(autouse=True)
    def _temp_table(self):
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

    def test_dispatch_insert_returns_rowcount(self):
        result = json.loads(
            db_dispatch(
                "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                ["test-value"],
            )
        )
        assert result["rowcount"] == 1
        assert "request_id" in result

    def test_dispatch_returns_request_id_uuid(self):
        import uuid

        result = json.loads(
            db_dispatch(
                "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                ["uuid-test"],
            )
        )
        uuid.UUID(result["request_id"])  # raises ValueError if not valid UUID

    def test_dispatch_update_rowcount(self):
        db_dispatch(
            "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
            ["to-update"],
        )
        result = json.loads(
            db_dispatch(
                "UPDATE public._mcp_dispatch_test SET val = %s WHERE val = %s",
                ["updated", "to-update"],
            )
        )
        assert result["rowcount"] == 1

    def test_dispatch_zero_rowcount_on_no_match(self):
        result = json.loads(
            db_dispatch(
                "DELETE FROM public._mcp_dispatch_test WHERE val = %s",
                ["__nonexistent__"],
            )
        )
        assert result["rowcount"] == 0

    def test_dispatch_routes_db_dispatch(self):
        raw = dispatch(
            "db_dispatch",
            {
                "sql": "INSERT INTO public._mcp_dispatch_test (val) VALUES (%s)",
                "params": ["via-dispatch"],
            },
        )
        data = json.loads(raw)
        assert data["rowcount"] == 1
