"""Tests for search_tools.py — unified fulltext search (T-librarian-fulltext-search)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── import guard ──────────────────────────────────────────────────────────────


from unseen_university.devices.librarian.tools import search_tools
from unseen_university.devices.librarian.tools import dispatch as lib_dispatch


# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_conn(rows: list[tuple], table: str = "palace"):
    """Build a mock psycopg2 connection that returns `rows` from cursor.fetchall()."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# ── SCHEMAS contract ──────────────────────────────────────────────────────────


class TestSchemas:
    def test_search_schema_present(self):
        names = [s["name"] for s in search_tools.SCHEMAS]
        assert "search" in names

    def test_search_schema_has_query_required(self):
        schema = next(s for s in search_tools.SCHEMAS if s["name"] == "search")
        assert "query" in schema["inputSchema"]["required"]

    def test_search_schema_registered_in_lib_tools(self):
        from unseen_university.devices.librarian.tools import SCHEMAS
        names = [s["name"] for s in SCHEMAS]
        assert "search" in names


# ── dispatch ──────────────────────────────────────────────────────────────────


class TestDispatch:
    def test_dispatch_routes_search(self):
        with patch.object(search_tools, "search", return_value="result") as mock_s:
            result = search_tools.dispatch("search", {"query": "granny"})
        assert result == "result"
        mock_s.assert_called_once()

    def test_dispatch_ignores_unknown_tool(self):
        result = search_tools.dispatch("not_a_tool", {})
        assert result is None

    def test_lib_dispatch_reaches_search(self):
        with patch.object(search_tools, "search", return_value="ok"):
            result = lib_dispatch("search", {"query": "test"})
        assert result == "ok"


# ── palace search ─────────────────────────────────────────────────────────────


class TestSearchPalace:
    def test_palace_hit_included(self):
        rows = [("palace.concepts.registered-dispatcher", "C-registered-dispatcher", "Three stacks: queue + registry + rules", 0.85)]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("registered dispatcher", source="palace")
        assert "palace" in result
        assert "palace.concepts.registered-dispatcher" in result
        assert "0.85" in result

    def test_palace_hit_contains_snippet(self):
        rows = [("palace.days.20260603", "Day 2026-06-03", "Granny dispatch shipped", 0.72)]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("granny", source="palace")
        assert "Granny dispatch shipped" in result

    def test_palace_no_results_returns_no_results_message(self):
        conn = _mock_conn([])
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("xyzzy-nonexistent", source="palace")
        assert "No results" in result


# ── memories / tickets search ─────────────────────────────────────────────────


class TestSearchMemories:
    def test_memory_hit_included(self):
        rows = [("GOAL_MURDERBOT", "Igor reads Murderbot for cultural context about agents", 0.68, "GOAL")]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("murderbot", source="memories")
        assert "memory" in result
        assert "GOAL_MURDERBOT" in result

    def test_ticket_hit_labelled_ticket(self):
        # Tickets now come from the filesystem ticket store, not clan.memories
        # (D-build-queue-filesystem-first-2026-06-19). Patch ticket_store.list.
        from unseen_university import ticket_store
        fake = [{
            "id": "T-granny-dispatch-role-map",
            "title": "Workers self-register roles via register_worker",
            "description": "", "tags": [], "status": "sprint",
        }]
        with patch.object(ticket_store, "list", return_value=fake):
            result = search_tools.search("register_worker", source="tickets")
        assert "ticket" in result
        assert "T-granny-dispatch-role-map" in result


# ── file search ───────────────────────────────────────────────────────────────


class TestSearchFiles:
    def _rg_output(self, lines: list[str]) -> MagicMock:
        proc = MagicMock()
        proc.stdout = "\n".join(lines)
        proc.returncode = 0
        return proc

    def test_file_hit_included(self):
        rg_lines = ["/home/akien/dev/src/UnseenUniversity/devices/granny/daemon.py:57:_ALERTED_IDS_FILE = _GRANNY_HOME"]
        with patch("subprocess.run", return_value=self._rg_output(rg_lines)):
            result = search_tools.search("_ALERTED_IDS_FILE", source="files")
        assert "file" in result
        assert "daemon.py:57" in result

    def test_file_search_graceful_when_rg_missing(self):
        import subprocess as sp
        with patch("subprocess.run", side_effect=FileNotFoundError("rg not found")):
            result = search_tools.search("anything", source="files")
        assert "No results" in result

    def test_file_hit_snippet_truncated(self):
        long_line = "x" * 200
        rg_lines = [f"/home/akien/dev/src/UnseenUniversity/devices/granny/daemon.py:1:{long_line}"]
        with patch("subprocess.run", return_value=self._rg_output(rg_lines)):
            result = search_tools.search("xxx", source="files")
        for line in result.split("\n"):
            if "file:" in line:
                assert len(line) < 300


# ── source filter ─────────────────────────────────────────────────────────────


class TestSourceFilter:
    def test_source_palace_only_skips_memories(self):
        conn = _mock_conn([])
        with patch.object(search_tools, "_conn", return_value=conn) as mock_conn_fn, \
             patch("subprocess.run") as mock_rg:
            search_tools.search("test", source="palace")
        mock_rg.assert_not_called()

    def test_source_files_only_skips_db(self):
        with patch.object(search_tools, "_conn") as mock_conn_fn, \
             patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)):
            search_tools.search("test", source="files")
        mock_conn_fn.assert_not_called()

    def test_unknown_source_treated_as_all(self):
        from unseen_university import ticket_store
        conn = _mock_conn([])
        with patch.object(search_tools, "_conn", return_value=conn), \
             patch.object(ticket_store, "list", return_value=[]), \
             patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)):
            result = search_tools.search("test", source="bogus")
        assert "No results" in result


# ── result format ─────────────────────────────────────────────────────────────


class TestResultFormat:
    def test_result_format_has_source_key_rank_snippet(self):
        rows = [("palace.test.node", "Test Node", "This is content", 0.91)]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("test", source="palace")
        # Expected: 'palace: palace.test.node (0.91) — "This is content"'
        assert "palace:" in result
        assert "(0.91)" in result
        assert '"' in result

    def test_limit_respected(self):
        rows = [(f"palace.node.{i}", f"Node {i}", "content", 0.5 - i * 0.01) for i in range(20)]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("test", source="palace", limit=5)
        assert result.count("\n") < 5  # at most 5 lines (4 newlines)

    def test_empty_query_returns_error(self):
        result = search_tools.search("")
        assert "query required" in result

    def test_results_sorted_by_rank_desc(self):
        rows = [
            ("palace.low", "Low", "low rank content", 0.3),
            ("palace.high", "High", "high rank content", 0.9),
        ]
        conn = _mock_conn(rows)
        with patch.object(search_tools, "_conn", return_value=conn):
            result = search_tools.search("content", source="palace")
        lines = result.strip().split("\n")
        assert "palace.high" in lines[0]
        assert "palace.low" in lines[1]
