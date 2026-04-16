"""
test_tool_discovery_semantic.py — T-tool-discovery-semantic

Tests that find_tool's synonym expansion resolves conversational queries
to the right tools.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition import push_sources  # noqa: E402 — triggers tool import
from wild_igor.igor.tools.memory_query import (  # noqa: E402
    _SYNONYM_MAP,
    find_tool,
)


def _top_tool(query: str) -> str:
    """Extract the top tool name from find_tool output."""
    result = find_tool(query)
    for line in result.split("\n"):
        if "score=" in line:
            return line.strip().split("(")[0].strip()
    return ""


def _tool_names(query: str) -> list[str]:
    """Extract all tool names from find_tool output."""
    names = []
    for line in find_tool(query).split("\n"):
        if "score=" in line:
            names.append(line.strip().split("(")[0].strip())
    return names


class TestSynonymExpansion:
    def test_reading_progress_finds_sessions(self):
        names = _tool_names("reading progress")
        assert "list_reading_sessions" in names

    def test_how_far_reading_finds_reading_tools(self):
        names = _tool_names("how far with reading")
        reading_tools = [n for n in names if "read" in n or "book" in n]
        assert len(reading_tools) >= 2

    def test_check_budget_finds_budget(self):
        names = _tool_names("check budget")
        assert any("budget" in n or "balance" in n for n in names)

    def test_what_books_finds_absorbed(self):
        names = _tool_names("what books have I read")
        assert "list_absorbed_books" in names

    def test_search_memory_still_works(self):
        names = _tool_names("search memory")
        assert "memory_search" in names

    def test_learn_queue_finds_drain(self):
        names = _tool_names("learn queue")
        assert any("learn" in n or "drain" in n or "queue" in n for n in names)

    def test_file_operations(self):
        names = _tool_names("read a file")
        assert any("file" in n or "read" in n for n in names)

    def test_goal_task(self):
        names = _tool_names("active goal")
        assert any("goal" in n or "task" in n for n in names)

    def test_synonym_map_has_reading(self):
        assert "reading" in _SYNONYM_MAP
        assert "book" in _SYNONYM_MAP["reading"]

    def test_empty_query(self):
        result = find_tool("")
        assert "too short" in result or "no matching" in result

    def test_scores_improved_over_baseline(self):
        result = find_tool("reading progress")
        for line in result.split("\n"):
            if "list_reading_sessions" in line and "score=" in line:
                score_str = line.split("score=")[1].split(")")[0]
                assert float(score_str) > 0.3
                return
        pytest.fail("list_reading_sessions not found in results")
