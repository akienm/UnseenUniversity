"""
test_provenance.py — T-provenance-coverage-enforcement

Tests for provenance metadata enforcement at the cortex.store() boundary.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory.provenance import (
    EXTENDED_PROVENANCE_KEYS,
    PROVENANCE_KEYS,
    ensure_provenance,
    provenance_report,
)


class TestEnsureProvenance:
    def test_stamps_deposited_at(self):
        meta = {}
        result = ensure_provenance(meta, source="reader")
        assert "deposited_at" in result
        assert len(result["deposited_at"]) > 10  # ISO timestamp

    def test_stamps_deposited_by_from_source(self):
        meta = {}
        result = ensure_provenance(meta, source="reading")
        assert result["deposited_by"] == "reading"

    def test_stamps_deposited_by_from_metadata_source(self):
        meta = {"source": "book_learner"}
        result = ensure_provenance(meta, source="")
        assert result["deposited_by"] == "book_learner"

    def test_unknown_when_no_source(self):
        meta = {}
        result = ensure_provenance(meta, source="")
        assert result["deposited_by"] == "unknown"

    def test_preserves_existing_deposited_by(self):
        meta = {"deposited_by": "self_training"}
        result = ensure_provenance(meta, source="reading")
        assert result["deposited_by"] == "self_training"  # not overwritten

    def test_preserves_existing_deposited_at(self):
        meta = {"deposited_at": "2026-01-01T00:00:00"}
        result = ensure_provenance(meta, source="test")
        assert result["deposited_at"] == "2026-01-01T00:00:00"

    def test_none_metadata_creates_dict(self):
        result = ensure_provenance(None, source="test")
        assert isinstance(result, dict)
        assert "deposited_by" in result

    def test_narrative_hint_used_for_logging(self):
        meta = {"_narrative_hint": "some memory text"}
        result = ensure_provenance(meta, source="")
        # hint should not be removed by ensure_provenance (caller removes it)
        assert "_narrative_hint" in result

    def test_full_provenance(self):
        meta = {
            "deposited_by": "reading",
            "deposited_at": "2026-04-17T12:00:00",
            "inference_tier": "cloud",
            "model_used": "claude-sonnet",
            "source_title": "On Intelligence",
            "source_author": "Jeff Hawkins",
            "source_ref": "/path/to/book.epub",
            "campaign_id": "reading-pass-2",
        }
        result = ensure_provenance(meta, source="reading")
        # All fields preserved, nothing overwritten
        assert result["model_used"] == "claude-sonnet"
        assert result["campaign_id"] == "reading-pass-2"


class TestProvenanceReport:
    def test_full_coverage(self):
        meta = {
            "deposited_by": "reader",
            "deposited_at": "2026-04-17",
            "inference_tier": "local",
            "model_used": "qwen2.5:7b",
            "source_title": "Test Book",
            "source_author": "Author",
            "source_ref": "/path",
            "campaign_id": "run-1",
        }
        report = provenance_report(meta)
        assert report["coverage"] == 1.0
        assert len(report["missing"]) == 0

    def test_partial_coverage(self):
        meta = {"deposited_by": "reader", "deposited_at": "2026-04-17"}
        report = provenance_report(meta)
        assert 0 < report["coverage"] < 1.0
        assert "model_used" in report["missing"]

    def test_zero_coverage(self):
        report = provenance_report({})
        assert report["coverage"] == 0.0
        assert "deposited_by" in report["missing"]


class TestStoreIntegration:
    """Verify provenance is stamped when cortex.store() is called."""

    def test_store_stamps_provenance(self):
        """Memory stored via cortex gets deposited_at and deposited_by."""
        from unittest.mock import MagicMock, patch

        from wild_igor.igor.memory.models import Memory, MemoryType

        mem = Memory(
            narrative="test provenance enforcement",
            memory_type=MemoryType.FACTUAL,
            source="test_source",
            metadata={"test_data": "true"},
        )

        # Mock cortex enough to call the provenance path
        cortex = MagicMock()
        conn = MagicMock()
        cortex._conn.return_value.__enter__.return_value = conn
        cortex._conn.return_value.__exit__.return_value = False
        cortex._instance_id = "test"
        cortex._habit_cache = None

        from wild_igor.igor.memory.cortex import Cortex

        # Call store directly on the class with our mock
        with patch.object(Cortex, "_maybe_calve"):
            Cortex.store(cortex, mem)

        # Check that provenance was stamped
        assert "deposited_at" in mem.metadata
        assert "deposited_by" in mem.metadata
        assert mem.metadata["deposited_by"] == "test_source"
        # _narrative_hint should be cleaned up
        assert "_narrative_hint" not in mem.metadata
