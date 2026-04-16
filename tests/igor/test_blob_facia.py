"""
test_blob_facia.py — T-blob-facia-and-tree-index (#443)

Tests for blob facia creation and tree registration.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory.blob_facia import (  # noqa: E402
    BLOB_TREE_PREFIX,
    ensure_blob_facia,
)


def _mock_cortex(mem_narrative="Test blob content", mem_metadata=None):
    cortex = MagicMock()
    mem = MagicMock()
    mem.id = "BLOB_001"
    mem.narrative = mem_narrative
    mem.metadata = mem_metadata or {"tags": ["test", "blob"]}
    cortex.get.return_value = mem
    cortex.store.return_value = mem
    cortex.add_interpretive_edge.return_value = None
    db_conn = MagicMock()
    cortex._db.return_value.__enter__ = MagicMock(return_value=db_conn)
    cortex._db.return_value.__exit__ = MagicMock(return_value=False)
    return cortex


class TestEnsureBlobFacia:
    def test_creates_facia_and_edge(self):
        cortex = _mock_cortex()
        result = ensure_blob_facia(cortex, "BLOB_001")
        assert result is not None
        cortex.store.assert_called()
        cortex.add_interpretive_edge.assert_called_once()
        edge_call = cortex.add_interpretive_edge.call_args
        assert edge_call.kwargs["to_id"] == "BLOB_001"
        assert edge_call.kwargs["direction"] == "contains"

    def test_returns_none_for_missing_memory(self):
        cortex = MagicMock()
        cortex.get.return_value = None
        result = ensure_blob_facia(cortex, "MISSING")
        assert result is None

    def test_skips_if_facia_already_exists(self):
        cortex = _mock_cortex(mem_metadata={"blob_facia_id": "EXISTING_FACIA"})
        result = ensure_blob_facia(cortex, "BLOB_001")
        assert result == "EXISTING_FACIA"
        cortex.store.assert_not_called()

    def test_uses_display_name(self):
        cortex = _mock_cortex()
        ensure_blob_facia(cortex, "BLOB_001", display_name="My Custom Name")
        stored_mem = cortex.store.call_args[0][0]
        assert "My Custom Name" in stored_mem.narrative

    def test_uses_tags_from_memory(self):
        cortex = _mock_cortex(mem_metadata={"tags": ["python", "design"]})
        ensure_blob_facia(cortex, "BLOB_001")
        stored_mem = cortex.store.call_args[0][0]
        assert stored_mem.metadata["blob_tags"] == ["python", "design"]

    def test_handles_store_failure(self):
        cortex = _mock_cortex()
        cortex.store.side_effect = RuntimeError("db error")
        result = ensure_blob_facia(cortex, "BLOB_001")
        assert result is None

    def test_facia_metadata_has_blob_memory_id(self):
        cortex = _mock_cortex()
        ensure_blob_facia(cortex, "BLOB_001")
        stored_mem = cortex.store.call_args[0][0]
        assert stored_mem.metadata["blob_memory_id"] == "BLOB_001"
        assert stored_mem.metadata["facia_role"] == "blob_index"

    def test_tree_prefix_constant(self):
        assert BLOB_TREE_PREFIX == "blob_"
