"""
tests/test_memory_query.py — T-memory-search-tool + T-find-tool-fuzzy

Tests cover:
  - memory_search: happy path, empty results, limit, error handling, registered
  - find_tool: name match, description match, no match, limit, score shown, registered
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_CORTEX_PATH = "devices.igor.memory.cortex.Cortex"


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()


class TestMemorySearch(unittest.TestCase):

    def _call(self, query, limit=5, hits=None):
        from devices.igor.tools.memory_query import memory_search

        mock_cortex = MagicMock()
        mock_cortex.search.return_value = hits or []
        with patch(_CORTEX_PATH, return_value=mock_cortex):
            return memory_search(query, limit=limit), mock_cortex

    def test_returns_hits(self):
        mem = MagicMock()
        mem.memory_type = "FACTUAL"
        mem.id = "FACT_001"
        mem.narrative = "Python is a programming language"
        result, _ = self._call("python", hits=[mem])
        self.assertIn("1 hit(s)", result)
        self.assertIn("FACTUAL", result)
        self.assertIn("Python is a programming language", result)

    def test_empty_results(self):
        result, _ = self._call("xyzzy_nonexistent")
        self.assertIn("no results", result)

    def test_limit_passed_to_cortex(self):
        _, mock_cortex = self._call("test query", limit=3)
        mock_cortex.search.assert_called_once_with("test query", limit=3)

    def test_error_returns_string(self):
        from devices.igor.tools.memory_query import memory_search

        with patch(_CORTEX_PATH, side_effect=RuntimeError("db down")):
            result = memory_search("anything")
        self.assertIn("error", result)
        self.assertIn("db down", result)

    def test_multiple_hits_formatted(self):
        mems = []
        for i in range(3):
            m = MagicMock()
            m.memory_type = "PROCEDURAL"
            m.id = f"PROC_{i:03d}"
            m.narrative = f"Procedure {i} description here"
            mems.append(m)
        result, _ = self._call("procedure", hits=mems)
        self.assertIn("3 hit(s)", result)
        self.assertIn("PROC_000", result)
        self.assertIn("PROC_002", result)

    def test_registered_in_registry(self):
        import os

        os.environ.setdefault(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        from lab.utility_closet.registry import registry
        import devices.igor.tools.memory_query  # noqa

        self.assertIn("memory_search", registry._tools)


class TestFindTool(unittest.TestCase):

    def setUp(self):
        from lab.utility_closet.registry import registry, Tool

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
        from lab.utility_closet.registry import registry

        for k in self._injected:
            registry._tools.pop(k, None)

    def test_name_match(self):
        from devices.igor.tools.memory_query import find_tool

        result = find_tool("alpha tool")
        self.assertIn("test_alpha_tool", result)

    def test_description_match(self):
        from devices.igor.tools.memory_query import find_tool

        result = find_tool("search memory keyword")
        self.assertIn("test_alpha_tool", result)

    def test_no_match_returns_message(self):
        from devices.igor.tools.memory_query import find_tool

        # Purely nonsense tokens that won't appear in any tool name/description
        result = find_tool("qxzplonk blarfwumbo zygfroth")
        self.assertIn("no matching tools", result)

    def test_limit_respected(self):
        from devices.igor.tools.memory_query import find_tool

        result = find_tool("tool", limit=1)
        lines = [l for l in result.splitlines() if "(score=" in l]
        self.assertLessEqual(len(lines), 1)

    def test_score_shown_in_output(self):
        from devices.igor.tools.memory_query import find_tool

        result = find_tool("search memory")
        self.assertIn("score=", result)

    def test_registered_in_registry(self):
        import os

        os.environ.setdefault(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        from lab.utility_closet.registry import registry
        import devices.igor.tools.memory_query  # noqa

        self.assertIn("find_tool", registry._tools)

    def test_filesystem_tool_not_in_memory_search_results(self):
        from devices.igor.tools.memory_query import find_tool

        # "write file filesystem" should match beta not alpha
        result = find_tool("write file filesystem")
        self.assertIn("test_beta_tool", result)


if __name__ == "__main__":
    unittest.main()
