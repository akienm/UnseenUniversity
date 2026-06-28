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

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "root-1")
        assert result == 42

    def test_returns_zero_on_missing(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "nonexistent")
        assert result == 0

    def test_returns_zero_on_error(self):
        cortex, conn = _mock_cortex()
        conn.execute.side_effect = Exception("db boom")

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.tree_size(cortex, "x")
        assert result == 0


class TestFindTreeRoot:
    def test_returns_root_id(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("root-abc",)

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex._find_tree_root(cortex, "leaf-1")
        assert result == "root-abc"

    def test_returns_self_when_already_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex._find_tree_root(cortex, "already-root")
        assert result == "already-root"


class TestDeepestChild:
    def test_returns_deepest_node(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("deep-leaf",)

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "root-1")
        assert result == "deep-leaf"

    def test_returns_none_when_root_is_deepest(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("root-1",)

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "root-1")
        assert result is None

    def test_returns_none_on_error(self):
        cortex, conn = _mock_cortex()
        conn.execute.side_effect = Exception("db boom")

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex._deepest_child(cortex, "x")
        assert result is None


class TestCalveSubtree:
    def test_calves_node_from_parent(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("parent-abc",)
        cortex.tree_size = MagicMock(return_value=15)
        cortex._find_tree_root = MagicMock(return_value="root-xyz")

        from unseen_university.devices.igor.memory.cortex import Cortex

        with patch("unseen_university.devices.igor.memory.blob_facia.ensure_blob_facia") as mock_facia:
            result = Cortex.calve_subtree(cortex, "child-node")
        assert result["new_root_id"] == "child-node"
        assert result["subtree_count"] == 15
        assert result["old_parent_id"] == "parent-abc"
        cortex.write_ring.assert_called_once()
        assert mock_facia.call_count == 2
        facia_root_ids = [c.args[1] for c in mock_facia.call_args_list]
        assert "child-node" in facia_root_ids
        assert "root-xyz" in facia_root_ids

    def test_calve_node_not_found(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = None

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "nonexistent")
        assert "error" in result

    def test_calve_already_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = (None,)

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "already-root")
        assert "error" in result
        assert "already a root" in result["error"]

    def test_calve_empty_parent_is_root(self):
        cortex, conn = _mock_cortex()
        conn.execute.return_value.fetchone.return_value = ("",)

        from unseen_university.devices.igor.memory.cortex import Cortex

        result = Cortex.calve_subtree(cortex, "empty-parent")
        assert "error" in result
        assert "already a root" in result["error"]

    def test_calve_refuses_core_pattern_nodes(self):
        """CP nodes, ID nodes, ROOT, PROC nodes must never be calved."""
        cortex, conn = _mock_cortex()
        from unseen_university.devices.igor.memory.cortex import Cortex

        cortex._CALVING_PROTECTED = Cortex._CALVING_PROTECTED
        for node_id in ("CP1", "CP3", "CP6", "ID1", "ID14", "ROOT", "PROC1"):
            result = Cortex.calve_subtree(cortex, node_id)
            assert "error" in result, f"{node_id} should be protected"
            assert "protected" in result["error"], f"{node_id}: {result}"


class TestMaybeCalveProtection:
    def test_skips_core_pattern_memory(self):
        import os
        from unittest.mock import patch as _patch

        cortex, conn = _mock_cortex()
        from unseen_university.devices.igor.memory.cortex import Cortex

        cortex._CALVING_PROTECTED = Cortex._CALVING_PROTECTED
        memory = MagicMock()
        memory.id = "CP3"
        memory.parent_id = "ROOT"
        with _patch.dict(os.environ, {"IGOR_CALVING_ENABLED": "true"}):
            Cortex._maybe_calve(cortex, memory)
        cortex.tree_size.assert_not_called()

    def test_skips_root_tree(self):
        import os
        from unittest.mock import patch as _patch

        from unseen_university.devices.igor.memory.cortex import Cortex

        cortex, conn = _mock_cortex()
        cortex._find_tree_root = MagicMock(return_value="ROOT")
        cortex._CALVING_PROTECTED = Cortex._CALVING_PROTECTED

        memory = MagicMock()
        memory.id = "some-node"
        memory.parent_id = "CP4"
        with _patch.dict(os.environ, {"IGOR_CALVING_ENABLED": "true"}):
            Cortex._maybe_calve(cortex, memory)
        cortex.tree_size.assert_not_called()


# ── _maybe_calve trigger ─────────────────────────────────────────────────────


class TestMaybeCalve:
    def test_skips_when_disabled(self):
        import os

        cortex, conn = _mock_cortex()
        os.environ.pop("IGOR_CALVING_ENABLED", None)

        from unseen_university.devices.igor.memory.cortex import Cortex

        memory = MagicMock()
        memory.id = "node-1"
        memory.parent_id = "parent-1"
        Cortex._maybe_calve(cortex, memory)
        # tree_size should never be called
        cortex.tree_size.assert_not_called()

    def test_skips_when_under_threshold(self):
        import os
        from unittest.mock import patch as _patch

        cortex, conn = _mock_cortex()
        cortex._find_tree_root = MagicMock(return_value="root-1")
        cortex.tree_size = MagicMock(return_value=500)

        from unseen_university.devices.igor.memory.cortex import Cortex

        memory = MagicMock()
        memory.id = "node-1"
        memory.parent_id = "parent-1"
        with _patch.dict(os.environ, {"IGOR_CALVING_ENABLED": "true"}):
            Cortex._maybe_calve(cortex, memory)
        cortex.calve_subtree.assert_not_called()

    def test_calves_when_over_threshold(self):
        import os
        from unittest.mock import patch as _patch

        cortex, conn = _mock_cortex()
        cortex._find_tree_root = MagicMock(return_value="root-1")
        cortex.tree_size = MagicMock(return_value=1500)
        cortex._deepest_child = MagicMock(return_value="deep-leaf")
        conn.execute.return_value.fetchone.return_value = ("mid-node",)
        cortex.calve_subtree = MagicMock(
            return_value={"new_root_id": "mid-node", "subtree_count": 200}
        )

        from unseen_university.devices.igor.memory.cortex import Cortex

        memory = MagicMock()
        memory.id = "node-1"
        memory.parent_id = "parent-1"
        with _patch.dict(os.environ, {"IGOR_CALVING_ENABLED": "true"}):
            Cortex._maybe_calve(cortex, memory)
        cortex.calve_subtree.assert_called_once_with("mid-node")

    def test_custom_threshold_via_env(self):
        import os
        from unittest.mock import patch as _patch

        cortex, conn = _mock_cortex()
        cortex._find_tree_root = MagicMock(return_value="root-1")
        cortex.tree_size = MagicMock(return_value=600)
        cortex._deepest_child = MagicMock(return_value="deep-leaf")
        conn.execute.return_value.fetchone.return_value = ("mid-node",)
        cortex.calve_subtree = MagicMock(
            return_value={"new_root_id": "mid-node", "subtree_count": 50}
        )

        from unseen_university.devices.igor.memory.cortex import Cortex

        memory = MagicMock()
        memory.id = "node-1"
        memory.parent_id = "parent-1"
        with _patch.dict(
            os.environ,
            {"IGOR_CALVING_ENABLED": "true", "IGOR_CALVING_THRESHOLD": "500"},
        ):
            Cortex._maybe_calve(cortex, memory)
        cortex.calve_subtree.assert_called_once()
