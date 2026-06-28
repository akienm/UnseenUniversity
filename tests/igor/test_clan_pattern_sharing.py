"""T-clan-pattern-sharing: export/import round-trip preserves graph structure.

Verifies:
  - export→import round-trip preserves node count + edge structure
  - import into a non-empty graph doesn't clobber existing nodes
  - missing root returns error string
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_memory(id_: str, narrative: str = "", parent_id: str = None, children=None):
    from unseen_university.devices.igor.memory.models import Memory, MemoryType

    m = Memory(id=id_, narrative=narrative or id_, memory_type=MemoryType.FACTUAL)
    m.parent_id = parent_id
    m.children_ids = children or []
    m.metadata = {}
    m.payload = None
    m.valence = 0.0
    m.arousal = 0.5
    m.source = "test"
    m.certainty = 1.0
    return m


class TestExportImportRoundTrip:
    def test_round_trip_preserves_node_count(self):
        """Export from cortex A, import into cortex B → same node count."""
        from unseen_university.devices.igor.memory.engram_export import export_engram, import_engram

        # Cortex A with a 3-node tree
        root = _make_memory("ROOT", "root node", children=["CHILD1", "CHILD2"])
        c1 = _make_memory("CHILD1", "child one", parent_id="ROOT")
        c2 = _make_memory("CHILD2", "child two", parent_id="ROOT")

        cortex_a = MagicMock()
        store = {"ROOT": root, "CHILD1": c1, "CHILD2": c2}
        cortex_a.get = MagicMock(side_effect=lambda nid: store.get(nid))
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(fetchall=MagicMock(return_value=[]))
                )
            )
        )
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex_a._conn = MagicMock(return_value=conn_ctx)

        template = export_engram(cortex_a, "ROOT")
        assert template is not None
        assert len(template.nodes) == 3

        # Cortex B is empty
        cortex_b = MagicMock()
        cortex_b.get = MagicMock(return_value=None)
        cortex_b.store = MagicMock()

        json_str = template.to_json()
        result = import_engram(cortex_b, json_str)

        assert result["imported"] == 3
        assert result["skipped"] == 0

    def test_import_skips_existing_nodes(self):
        """Import does not overwrite nodes that already exist in the target graph."""
        from unseen_university.devices.igor.memory.engram_export import export_engram, import_engram

        root = _make_memory("ROOT2", "root", children=["CHILD3"])
        child = _make_memory("CHILD3", "child", parent_id="ROOT2")

        cortex_a = MagicMock()
        store = {"ROOT2": root, "CHILD3": child}
        cortex_a.get = MagicMock(side_effect=lambda nid: store.get(nid))
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(fetchall=MagicMock(return_value=[]))
                )
            )
        )
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex_a._conn = MagicMock(return_value=conn_ctx)

        template = export_engram(cortex_a, "ROOT2")
        json_str = template.to_json()

        # Cortex B already has ROOT2
        cortex_b = MagicMock()
        cortex_b.get = MagicMock(
            side_effect=lambda nid: _make_memory(nid) if nid == "ROOT2" else None
        )
        cortex_b.store = MagicMock()

        result = import_engram(cortex_b, json_str)
        assert result["skipped"] >= 1
        # store should only be called for non-existing nodes
        stored_ids = {call.args[0].id for call in cortex_b.store.call_args_list}
        assert "ROOT2" not in stored_ids

    def test_export_missing_root_returns_none(self):
        """export_engram returns None when root_id not found."""
        from unseen_university.devices.igor.memory.engram_export import export_engram

        cortex = MagicMock()
        cortex.get = MagicMock(return_value=None)

        result = export_engram(cortex, "NONEXISTENT")
        assert result is None

    def test_share_pattern_tool_export_missing_root(self):
        """export_pattern tool returns error string for unknown root."""
        from unseen_university.devices.igor.tools.share_pattern import export_pattern

        cortex_mock = MagicMock()
        cortex_mock.get = MagicMock(return_value=None)

        import unseen_university.devices.igor.tools.share_pattern as sp_mod

        original = sp_mod._get_cortex
        sp_mod._get_cortex = MagicMock(return_value=cortex_mock)
        try:
            result = export_pattern("MISSING_NODE")
            assert result.startswith("error:")
        finally:
            sp_mod._get_cortex = original

    def test_json_round_trip_edge_structure(self):
        """Exported JSON parses back to correct node list."""
        from unseen_university.devices.igor.memory.engram_export import EngramTemplate, export_engram

        root = _make_memory("RTEST", "root", children=["C1"])
        c1 = _make_memory("C1", "child", parent_id="RTEST")

        cortex_a = MagicMock()
        store = {"RTEST": root, "C1": c1}
        cortex_a.get = MagicMock(side_effect=lambda nid: store.get(nid))
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(fetchall=MagicMock(return_value=[]))
                )
            )
        )
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex_a._conn = MagicMock(return_value=conn_ctx)

        template = export_engram(cortex_a, "RTEST")
        json_str = template.to_json()

        recovered = EngramTemplate.from_json(json_str)
        assert recovered.root_id == "RTEST"
        recovered_ids = {n["id"] for n in recovered.nodes}
        assert {"RTEST", "C1"} == recovered_ids
