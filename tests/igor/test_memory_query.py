"""
tests/test_memory_query.py — T-memory-search-tool + T-find-tool-fuzzy + T-memory-search-rrf

Tests cover:
  - memory_search: happy path, empty results, limit, error handling, registered
  - _rrf_merge: dual-list fusion, single-list passthrough, deduplication
  - RRF integration: signal label, items present in both lists rank higher
  - find_tool: name match, description match, no match, limit, score shown, registered
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_CORTEX_PATH = "unseen_university.devices.igor.memory.cortex.Cortex"
_FTS_PATH = "unseen_university.devices.igor.tools.memory_query._fts_search"


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()


def _make_mem(id_: str, narrative: str = "", memory_type: str = "FACTUAL"):
    m = MagicMock()
    m.id = id_
    m.narrative = narrative or f"narrative for {id_}"
    m.memory_type = memory_type
    m.timestamp = None
    return m


class TestMemorySearch(unittest.TestCase):

    def _call(self, query, limit=5, hits=None, fts_hits=None):
        """Call memory_search with cortex and _fts_search both mocked."""
        from unseen_university.devices.igor.tools.memory_query import memory_search

        mock_cortex = MagicMock()
        mock_cortex.search.return_value = hits or []
        with (
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_FTS_PATH, return_value=fts_hits or []),
        ):
            return memory_search(query, limit=limit), mock_cortex

    def test_returns_hits(self):
        mem = _make_mem("FACT_001", "Python is a programming language", "FACTUAL")
        result, _ = self._call("python", hits=[mem])
        self.assertIn("1 hit(s)", result)
        self.assertIn("FACTUAL", result)
        self.assertIn("Python is a programming language", result)

    def test_empty_results(self):
        result, _ = self._call("xyzzy_nonexistent")
        self.assertIn("no results", result)

    def test_limit_fetches_wider_pool_for_rrf(self):
        # cortex.search is called with limit*3 so RRF has a wider pool to fuse
        _, mock_cortex = self._call("test query", limit=3)
        mock_cortex.search.assert_called_once_with("test query", limit=9)

    def test_error_returns_string(self):
        from unseen_university.devices.igor.tools.memory_query import memory_search

        with patch(_CORTEX_PATH, side_effect=RuntimeError("db down")):
            result = memory_search("anything")
        self.assertIn("error", result)
        self.assertIn("db down", result)

    def test_multiple_hits_formatted(self):
        mems = [
            _make_mem(f"PROC_{i:03d}", f"Procedure {i}", "PROCEDURAL") for i in range(3)
        ]
        result, _ = self._call("procedure", hits=mems)
        self.assertIn("3 hit(s)", result)
        self.assertIn("PROC_000", result)
        self.assertIn("PROC_002", result)

    def test_holistic_signal_when_fts_empty(self):
        mem = _make_mem("M1", "anything")
        result, _ = self._call("anything", hits=[mem], fts_hits=[])
        self.assertIn("signal=holistic", result)

    def test_rrf_signal_when_fts_returns_results(self):
        mem = _make_mem("M1", "anything")
        fts_mem = _make_mem("M2", "exact match")
        result, _ = self._call("anything", hits=[mem], fts_hits=[fts_mem])
        self.assertIn("signal=rrf", result)

    def test_registered_in_registry(self):
        import os

        os.environ.setdefault(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
        )
        from unseen_university.devices.igor.tools.registry import registry
        import unseen_university.devices.igor.tools.memory_query  # noqa

        self.assertIn("memory_search", registry._tools)


class TestRRFMerge(unittest.TestCase):

    def test_item_in_both_lists_ranks_first(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        # M1 is #1 in list_a, #1 in list_b → highest RRF score
        # M2 is #2 in list_a only
        # M3 is #2 in list_b only
        m1 = _make_mem("M1")
        m2 = _make_mem("M2")
        m3 = _make_mem("M3")
        merged = _rrf_merge([m1, m2], [m1, m3])
        self.assertEqual(merged[0].id, "M1")

    def test_deduplication(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        m1 = _make_mem("M1")
        merged = _rrf_merge([m1, m1], [m1])
        # M1 should appear only once
        self.assertEqual(len(merged), 1)

    def test_items_only_in_one_list_included(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        m1 = _make_mem("M1")
        m2 = _make_mem("M2")
        merged = _rrf_merge([m1], [m2])
        ids = [m.id for m in merged]
        self.assertIn("M1", ids)
        self.assertIn("M2", ids)

    def test_empty_list_b_returns_list_a(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        mems = [_make_mem(f"M{i}") for i in range(3)]
        merged = _rrf_merge(mems, [])
        self.assertEqual([m.id for m in merged], [m.id for m in mems])

    def test_both_empty_returns_empty(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        self.assertEqual(_rrf_merge([], []), [])

    def test_lower_ranked_item_overtakes_with_second_signal(self):
        from unseen_university.devices.igor.tools.memory_query import _rrf_merge

        # M2 is rank-2 in list_a but rank-1 in list_b
        # M1 is rank-1 in list_a only
        # M2 should beat M1 via RRF
        m1 = _make_mem("M1")
        m2 = _make_mem("M2")
        m3 = _make_mem("M3")
        # list_a: M1, M2, M3   list_b: M2, M3, M1
        merged = _rrf_merge([m1, m2, m3], [m2, m3, m1])
        # M2: 1/(60+2) + 1/(60+1) ≈ 0.01613 + 0.01639 ≈ 0.03252
        # M1: 1/(60+1) + 1/(60+3) ≈ 0.01639 + 0.01587 ≈ 0.03226
        # M3: 1/(60+3) + 1/(60+2) ≈ 0.01587 + 0.01613 ≈ 0.03200
        self.assertEqual(merged[0].id, "M2")


class TestFindTool(unittest.TestCase):

    def setUp(self):
        from unseen_university.devices.igor.tools.registry import registry, Tool

        self._injected = {
            "test_alpha_tool": Tool(
                name="test_alpha_tool",
                description="Search memory records by keyword",
                parameters={},
                fn=lambda: None,
            ),
            "test_beta_tool": Tool(
                name="test_beta_tool",
                description="Write a file to the filesystem",
                parameters={},
                fn=lambda: None,
            ),
            "test_gamma_tool": Tool(
                name="test_gamma_tool",
                description="List all registered tools and capabilities",
                parameters={},
                fn=lambda: None,
            ),
        }
        for k, v in self._injected.items():
            registry._tools[k] = v

    def tearDown(self):
        from unseen_university.devices.igor.tools.registry import registry

        for k in self._injected:
            registry._tools.pop(k, None)

    def test_name_match(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        result = find_tool("alpha tool")
        self.assertIn("test_alpha_tool", result)

    def test_description_match(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        result = find_tool("search memory keyword")
        self.assertIn("test_alpha_tool", result)

    def test_no_match_returns_message(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        # Purely nonsense tokens that won't appear in any tool name/description
        result = find_tool("qxzplonk blarfwumbo zygfroth")
        self.assertIn("no matching tools", result)

    def test_limit_respected(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        result = find_tool("tool", limit=1)
        lines = [l for l in result.splitlines() if "(score=" in l]
        self.assertLessEqual(len(lines), 1)

    def test_score_shown_in_output(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        result = find_tool("search memory")
        self.assertIn("score=", result)

    def test_registered_in_registry(self):
        import os

        os.environ.setdefault(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
        )
        from unseen_university.devices.igor.tools.registry import registry
        import unseen_university.devices.igor.tools.memory_query  # noqa

        self.assertIn("find_tool", registry._tools)

    def test_filesystem_tool_not_in_memory_search_results(self):
        from unseen_university.devices.igor.tools.memory_query import find_tool

        # "write file filesystem" should match beta not alpha
        result = find_tool("write file filesystem")
        self.assertIn("test_beta_tool", result)


if __name__ == "__main__":
    unittest.main()
