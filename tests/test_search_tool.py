"""Tests for search_tool.py backends and folder_indexer.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.librarian.tools.search_tool import SearchResult, _search_git, _search_indexed, _search_palace, search


# ── Palace backend ─────────────────────────────────────────────────────────────


class TestSearchPalace:
    def _conn_with_rows(self, rows):
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = rows
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.cursor.return_value = cur
        return conn

    def test_palace_returns_results(self):
        rows = [{"path": "palace/test", "title": "Test", "content": "hello world", "rank": 0.9}]
        conn = self._conn_with_rows(rows)
        with patch("psycopg2.connect", return_value=conn):
            results = _search_palace("hello")
        assert len(results) == 1
        assert results[0].source == "palace"
        assert results[0].id == "palace/test"

    def test_palace_db_failure_returns_empty(self):
        with patch("psycopg2.connect", side_effect=Exception("DB down")):
            results = _search_palace("anything")
        assert results == []


# ── Indexed backend ────────────────────────────────────────────────────────────


class TestSearchIndexed:
    def test_indexed_returns_results(self):
        fake_rows = [{"path": "/some/file.py", "chunk_index": 0, "chunk_text": "def foo():", "rank": 0.8}]
        with patch("devices.scraps.jobs.folder_indexer.search_indexed", return_value=fake_rows):
            results = _search_indexed("foo")
        assert len(results) == 1
        assert results[0].source == "indexed"
        assert "/some/file.py#0" == results[0].id

    def test_indexed_failure_returns_empty(self):
        with patch("devices.scraps.jobs.folder_indexer.search_indexed", side_effect=Exception("nope")):
            results = _search_indexed("foo")
        assert results == []


# ── Git backend ────────────────────────────────────────────────────────────────


class TestSearchGit:
    def test_git_grep_returns_commits(self):
        fake_stdout = "abc123def456|Fix the caching bug|2026-06-04T12:00:00+00:00\n"
        mock_result = MagicMock(returncode=0, stdout=fake_stdout)
        with patch("subprocess.run", return_value=mock_result):
            results = _search_git("caching")
        assert len(results) == 1
        assert results[0].source == "git"
        assert "abc123de" in results[0].snippet

    def test_git_no_matches_returns_empty(self):
        mock_result = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            results = _search_git("zzzzznotfound")
        assert results == []

    def test_git_failure_returns_empty(self):
        with patch("subprocess.run", side_effect=Exception("git not found")):
            results = _search_git("anything")
        assert results == []


# ── Union search ───────────────────────────────────────────────────────────────


class TestSearchUnion:
    def test_empty_query_returns_empty(self):
        import asyncio
        results = asyncio.run(search(""))
        assert results == []

    def test_source_filter_palace(self):
        import asyncio
        with patch("devices.librarian.tools.search_tool._search_palace", return_value=[]) as mock_palace, \
             patch("devices.librarian.tools.search_tool._search_indexed") as mock_idx, \
             patch("devices.librarian.tools.search_tool._search_git") as mock_git:
            asyncio.run(search("test", source="palace"))
        mock_palace.assert_called_once()
        mock_idx.assert_not_called()
        mock_git.assert_not_called()


# ── Folder indexer ─────────────────────────────────────────────────────────────


class TestFolderIndexer:
    def test_run_indexer_no_paths_returns_zero(self):
        from devices.scraps.jobs.folder_indexer import run_indexer
        result = run_indexer(paths=[])
        assert result["indexed"] == 0
        assert result["skipped"] == 0

    def test_chunk_file_yields_chunks(self, tmp_path):
        from devices.scraps.jobs.folder_indexer import _chunk_file
        f = tmp_path / "test.py"
        f.write_text("x" * 2500)
        chunks = list(_chunk_file(f, chunk_size=800))
        assert len(chunks) == 4
        for idx, text in chunks:
            assert len(text) <= 800

    def test_chunk_file_empty_file_yields_nothing(self, tmp_path):
        from devices.scraps.jobs.folder_indexer import _chunk_file
        f = tmp_path / "empty.txt"
        f.write_text("")
        chunks = list(_chunk_file(f))
        assert chunks == []
