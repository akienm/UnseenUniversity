"""
test_calving.py — T-calving-split-op

Tests for cortex.calve_subtree, tree_size, _find_tree_root, _deepest_child.
Uses mocked cortex._conn to simulate tree structures.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _mock_cortex():
    """Build a mock Cortex with _conn context manager."""
    cortex = MagicMock()
    conn = MagicMock()
    cortex._conn.return_value.__enter__.return_value = conn
    cortex._conn.return_value.__exit__.return_value = False
    cortex.write_ring = MagicMock()
    return cortex, conn


class TestTreeSize:
    def test_returns_count(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = (42,)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "root-1")
        assert result == 42

    def test_returns_zero_on_missing(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "nonexistent")
        assert result == 0

    def test_returns_zero_on_error(self):
        cortex, conn = _mock_cortex()
        conn.execute.side_effect = Exception("db boom")

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "x")
        assert result == 0


class TestFindTreeRoot:
    def test_returns_root_id(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("root-abc",)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex._find_tree_root(cortex, "leaf-1")
        assert result == "root-abc"

    def test_returns_self_when_already_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex._find_tree_root(cortex, "already-root")
        assert result == "already-root"


class TestDeepestChild:
    def test_returns_deepest_node(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("deep-leaf",)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "root-1")
        assert result == "deep-leaf"

    def test_returns_none_when_root_is_deepest(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("root-1",)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "root-1")
        assert result is None

    def test_returns_none_on_error(self):
        cortex, conn = _mock_cortex()
        conn.execute.side_effect = Exception("db boom")

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "x")
        assert result is None


class TestCalveSubtree:
    def test_calves_node_from_parent(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("parent-abc",)
        cortex.tree_size = MagicMock(return_value=15)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "child-node")
        assert result["new_root_id"] == "child-node"
        assert result["subtree_count"] == 15
        assert result["old_parent_id"] == "parent-abc"
        cortex.write_ring.assert_called_once()

    def test_calve_node_not_found(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "nonexistent")
        assert "error" in result

    def test_calve_already_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = (None,)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "already-root")
        assert "error" in result
        assert "already a root" in result["error"]

    def test_calve_empty_parent_is_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("",)

        from wild_igor.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "empty-parent")
        assert "error" in result
        assert "already a root" in result["error"]
