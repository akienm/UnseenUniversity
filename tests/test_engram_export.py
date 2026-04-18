"""
test_engram_export.py — T-engram-portability

Tests for engram export/import and dependency scanning.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory.engram_export import (
    EngramTemplate,
    _scan_dependencies,
    export_engram,
    import_engram,
)


class TestScanDependencies:
    def test_finds_code_refs(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {"code_ref": "tools.pe_chain:pe_plan"},
                "narrative": "",
                "payload": None,
            }
        ]
        deps = _scan_dependencies(nodes)
        assert "tools.pe_chain:pe_plan" in deps["tool_refs"]

    def test_finds_mcpcall_tools(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {},
                "narrative": "",
                "payload": {"cells": [["MCPCALL", "store_memory", "_args", "_out"]]},
            }
        ]
        deps = _scan_dependencies(nodes)
        assert "store_memory" in deps["tool_refs"]

    def test_finds_external_branch_targets(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {},
                "narrative": "",
                "payload": {"cells": [["BRANCHIF", True, "EXTERNAL_NODE"]]},
            },
        ]
        deps = _scan_dependencies(nodes)
        assert "EXTERNAL_NODE" in deps["memory_refs"]

    def test_internal_targets_not_dependencies(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {},
                "narrative": "",
                "payload": {"cells": [["BRANCHIF", True, "N2"]]},
            },
            {"id": "N2", "metadata": {}, "narrative": "", "payload": None},
        ]
        deps = _scan_dependencies(nodes)
        assert "N2" not in deps["memory_refs"]

    def test_finds_channel_refs(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {},
                "narrative": "emit to comms://shared channel",
                "payload": None,
            }
        ]
        deps = _scan_dependencies(nodes)
        assert "comms://shared" in deps["channel_refs"]

    def test_finds_env_vars(self):
        nodes = [
            {
                "id": "N1",
                "metadata": {},
                "narrative": "check IGOR_CLOUD_TRAINING_ENABLED flag",
                "payload": None,
            }
        ]
        deps = _scan_dependencies(nodes)
        assert "IGOR_CLOUD_TRAINING_ENABLED" in deps["env_vars"]

    def test_finds_parent_outside_subgraph(self):
        nodes = [
            {
                "id": "N1",
                "parent_id": "CP1",
                "metadata": {},
                "narrative": "",
                "payload": None,
            }
        ]
        deps = _scan_dependencies(nodes)
        assert "CP1" in deps["memory_refs"]

    def test_empty_nodes(self):
        deps = _scan_dependencies([])
        assert deps == {
            "tool_refs": [],
            "memory_refs": [],
            "channel_refs": [],
            "env_vars": [],
        }


class TestEngramTemplate:
    def test_roundtrip_json(self):
        t = EngramTemplate(
            root_id="TEST_ROOT",
            nodes=[{"id": "TEST_ROOT", "narrative": "test"}],
            dependencies={"tool_refs": ["store_memory"]},
            exported_by="test",
        )
        json_str = t.to_json()
        t2 = EngramTemplate.from_json(json_str)
        assert t2.root_id == "TEST_ROOT"
        assert len(t2.nodes) == 1
        assert t2.dependencies["tool_refs"] == ["store_memory"]


class TestExportEngram:
    def test_export_single_node(self):
        cortex = MagicMock()
        node = MagicMock()
        node.id = "ENGRAM_TEST"
        node.narrative = "test engram"
        node.memory_type = MagicMock(value="PROCEDURAL")
        node.parent_id = "CP1"
        node.metadata = {"habit_type": "engram", "code_ref": "tools.test:fn"}
        node.payload = {"cells": []}
        node.children_ids = []
        node.valence = 0.0
        node.arousal = 0.0
        node.source = "seed"
        node.confidence = 1.0
        cortex.get.return_value = node

        conn = MagicMock()
        cortex._conn.return_value.__enter__.return_value = conn
        cortex._conn.return_value.__exit__.return_value = False
        conn.execute.return_value.fetchall.return_value = []

        template = export_engram(cortex, "ENGRAM_TEST", exported_by="test")
        assert template is not None
        assert template.root_id == "ENGRAM_TEST"
        assert len(template.nodes) == 1
        assert "tools.test:fn" in template.dependencies["tool_refs"]

    def test_export_nonexistent(self):
        cortex = MagicMock()
        cortex.get.return_value = None
        result = export_engram(cortex, "GHOST")
        assert result is None


class TestImportEngram:
    def test_import_new_nodes(self):
        cortex = MagicMock()
        cortex.get.return_value = None  # nothing exists yet
        cortex.store = MagicMock()

        template = EngramTemplate(
            root_id="IMP_ROOT",
            nodes=[
                {
                    "id": "IMP_ROOT",
                    "narrative": "imported",
                    "memory_type": "PROCEDURAL",
                    "parent_id": "CP1",
                    "metadata": {},
                    "payload": None,
                    "valence": 0,
                    "arousal": 0,
                    "source": "test",
                    "confidence": 1.0,
                },
            ],
        )
        result = import_engram(cortex, template.to_json())
        assert result["imported"] == 1
        assert result["skipped"] == 0

    def test_skip_existing_nodes(self):
        cortex = MagicMock()
        cortex.get.return_value = MagicMock()  # already exists
        cortex.store = MagicMock()

        template = EngramTemplate(
            root_id="EXISTS",
            nodes=[
                {
                    "id": "EXISTS",
                    "narrative": "x",
                    "memory_type": "PROCEDURAL",
                    "metadata": {},
                }
            ],
        )
        result = import_engram(cortex, template.to_json())
        assert result["imported"] == 0
        assert result["skipped"] == 1
