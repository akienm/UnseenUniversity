"""Tests for Librarian MCP tool registry — T-librarian-mcp-tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_datacenter.devices.librarian.tools import SCHEMAS, dispatch
from agent_datacenter.devices.librarian.tools import (
    db_tools,
    memory_tools,
    channel_tools,
    igor_tools,
)

# ── Schema inventory ──────────────────────────────────────────────────────────


class TestSchemaInventory:
    EXPECTED_TOOLS = {
        "db_query",
        "db_dispatch",
        "memory_search",
        "memory_get",
        "memory_list_by_type",
        "channel_read",
        "channel_send",
        "traces_recent",
        "traces_get",
        "tail_heat",
        "hot_nodes",
        "hot_attractors",
        "habit_list",
        "turn_trace_recent",
        "consult_sessions_recent",
        "wg_neighbors",
    }

    def test_all_expected_tools_present(self):
        names = {s["name"] for s in SCHEMAS}
        assert self.EXPECTED_TOOLS <= names

    def test_each_schema_has_required_fields(self):
        for schema in SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "inputSchema" in schema

    def test_dispatch_unknown_returns_error(self):
        result = dispatch("no_such_tool", {})
        assert "Unknown tool" in result


# ── db_tools ─────────────────────────────────────────────────────────────────


class TestDbTools:
    def _mock_q(self, rows):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = rows
        m.cursor.return_value = cursor
        return m

    def test_db_query_returns_json(self):
        fake_row = {"id": 1, "name": "test"}
        conn = self._mock_q([fake_row])
        from contextlib import contextmanager

        @contextmanager
        def _fake_get_conn(pg_url=None):
            yield conn

        with patch(
            "agent_datacenter.devices.librarian.tools.db_tools.get_conn", _fake_get_conn
        ):
            result = db_tools.db_query("SELECT 1", pg_url="postgresql://fake/db")
        data = json.loads(result)
        assert data["count"] == 1

    def test_db_dispatch_returns_rowcount(self):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.rowcount = 3
        conn.cursor.return_value = cursor
        from contextlib import contextmanager

        @contextmanager
        def _fake_get_conn(pg_url=None):
            yield conn

        with patch(
            "agent_datacenter.devices.librarian.tools.db_tools.get_conn", _fake_get_conn
        ):
            result = db_tools.db_dispatch(
                "DELETE FROM t", pg_url="postgresql://fake/db"
            )
        data = json.loads(result)
        assert data["rowcount"] == 3
        assert "request_id" in data

    def test_dispatch_routes_db_query(self):
        with patch(
            "agent_datacenter.devices.librarian.tools.db_tools.db_query",
            return_value='{"rows":[],"count":0}',
        ) as mock_fn:
            result = db_tools.dispatch("db_query", {"sql": "SELECT 1"})
        assert result is not None

    def test_dispatch_returns_none_for_unknown(self):
        assert db_tools.dispatch("memory_search", {}) is None


# ── memory_tools ─────────────────────────────────────────────────────────────


class TestMemoryTools:
    def test_memory_search_no_terms(self):
        result = memory_tools.memory_search("", pg_url="postgresql://fake/db")
        assert "No query terms" in result

    def test_memory_search_uses_fts(self):
        with patch("psycopg2.connect") as mock_connect:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            mock_connect.return_value = conn
            result = memory_tools.memory_search(
                "session memory", pg_url="postgresql://fake/db"
            )
        assert "No memories found" in result
        sql = cursor.execute.call_args[0][0]
        assert "plainto_tsquery" in sql
        assert "LIKE" not in sql

    def test_memory_get_not_found(self):
        with patch("psycopg2.connect") as mock_connect:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            mock_connect.return_value = conn
            result = memory_tools.memory_get(
                "nonexistent-id", pg_url="postgresql://fake/db"
            )
        assert "not found" in result

    def test_dispatch_routes_memory_get(self):
        with patch(
            "agent_datacenter.devices.librarian.tools.memory_tools.memory_get",
            return_value="ok",
        ) as mock_fn:
            result = memory_tools.dispatch("memory_get", {"memory_id": "abc"})
        assert result == "ok"

    def test_dispatch_returns_none_for_unknown(self):
        assert memory_tools.dispatch("db_query", {}) is None


# ── channel_tools ─────────────────────────────────────────────────────────────


class TestChannelTools:
    def test_channel_send_failure_returns_error_string(self):
        result = channel_tools.channel_send(
            "hello", channel="shared", cc_send_url="http://localhost:0/dead"
        )
        assert "failed" in result.lower() or "error" in result.lower()

    def test_channel_read_not_found(self):
        with patch("psycopg2.connect") as mock_connect:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            mock_connect.return_value = conn
            result = channel_tools.channel_read(pg_url="postgresql://fake/db")
        assert "No messages" in result

    def test_dispatch_routes_channel_send(self):
        with patch(
            "agent_datacenter.devices.librarian.tools.channel_tools.channel_send",
            return_value="sent",
        ) as mock_fn:
            result = channel_tools.dispatch("channel_send", {"content": "hi"})
        assert result == "sent"

    def test_dispatch_returns_none_for_unknown(self):
        assert channel_tools.dispatch("db_query", {}) is None


# ── igor_tools ────────────────────────────────────────────────────────────────


class TestIgorTools:
    def test_traces_recent_empty(self):
        with patch("psycopg2.connect") as mock_connect:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            mock_connect.return_value = conn
            result = igor_tools.traces_recent(pg_url="postgresql://fake/db")
        assert "No traces" in result

    def test_tail_heat_no_entries(self):
        with patch("psycopg2.connect") as mock_connect:
            conn = MagicMock()
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            cursor = MagicMock()
            cursor.__enter__ = MagicMock(return_value=cursor)
            cursor.__exit__ = MagicMock(return_value=False)
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            mock_connect.return_value = conn
            result = igor_tools.tail_heat("node-xyz", pg_url="postgresql://fake/db")
        assert "No tail entries" in result

    def test_turn_trace_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = igor_tools.turn_trace_recent()
        assert "No turn_trace log found" in result

    def test_consult_sessions_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = igor_tools.consult_sessions_recent()
        assert "No consults log" in result

    def test_dispatch_routes_tail_heat(self):
        with patch(
            "agent_datacenter.devices.librarian.tools.igor_tools.tail_heat",
            return_value="heat=1.0",
        ) as mock_fn:
            result = igor_tools.dispatch("tail_heat", {"node_id": "abc"})
        assert result == "heat=1.0"

    def test_dispatch_returns_none_for_unknown(self):
        assert igor_tools.dispatch("db_query", {}) is None


# ── MCP server dispatch ───────────────────────────────────────────────────────


class TestMcpServerDispatch:
    def test_tools_list_returns_all_schemas(self):
        import io
        from agent_datacenter.devices.librarian.mcp_server import _dispatch

        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = _dispatch(msg)
        assert resp["result"]["tools"] == SCHEMAS

    def test_tools_call_unknown_tool(self):
        from agent_datacenter.devices.librarian.mcp_server import _dispatch

        msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        }
        resp = _dispatch(msg)
        assert "Unknown tool" in resp["result"]["content"][0]["text"]

    def test_initialize_response(self):
        from agent_datacenter.devices.librarian.mcp_server import _dispatch

        msg = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {},
        }
        resp = _dispatch(msg)
        assert resp["result"]["serverInfo"]["name"] == "librarian"
