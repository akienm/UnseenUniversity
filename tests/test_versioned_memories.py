"""
test_versioned_memories.py — T-versioned-memories

Tests for memory versioning: snapshot current state as child on update.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory.versioning import (
    version_before_update,
    get_version_history,
)


def _mock_cortex_with_existing(existing_memory):
    cortex = MagicMock()
    cortex.get.return_value = existing_memory
    conn = MagicMock()
    cortex._conn.return_value.__enter__.return_value = conn
    cortex._conn.return_value.__exit__.return_value = False
    conn.execute.return_value.fetchone.return_value = (0,)  # no existing versions
    cortex.store = MagicMock()
    return cortex


def _mock_memory(id="TEST_V", narrative="original text", versioned=True):
    from wild_igor.igor.memory.models import Memory, MemoryType

    return Memory(
        id=id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        metadata={"versioned": versioned} if versioned else {},
    )


class TestVersionBeforeUpdate:
    def test_creates_version_snapshot(self):
        existing = _mock_memory(narrative="old text")
        new = _mock_memory(narrative="new text")
        cortex = _mock_cortex_with_existing(existing)

        result = version_before_update(cortex, new)
        assert result is not None
        assert result == "TEST_V_v001"
        cortex.store.assert_called_once()
        stored = cortex.store.call_args[0][0]
        assert stored.narrative == "old text"
        assert stored.parent_id == "TEST_V"
        assert stored.metadata["version_of"] == "TEST_V"
        assert stored.metadata["version_seq"] == 1

    def test_skips_when_not_versioned(self):
        new = _mock_memory(versioned=False)
        cortex = MagicMock()

        result = version_before_update(cortex, new)
        assert result is None
        cortex.store.assert_not_called()

    def test_skips_when_new_memory(self):
        new = _mock_memory()
        cortex = MagicMock()
        cortex.get.return_value = None  # doesn't exist yet

        result = version_before_update(cortex, new)
        assert result is None

    def test_skips_when_unchanged(self):
        existing = _mock_memory(narrative="same text")
        new = _mock_memory(narrative="same text")
        new.metadata = dict(existing.metadata)
        cortex = _mock_cortex_with_existing(existing)

        result = version_before_update(cortex, new)
        assert result is None

    def test_version_snapshot_removes_versioned_flag(self):
        existing = _mock_memory(narrative="v1")
        new = _mock_memory(narrative="v2")
        cortex = _mock_cortex_with_existing(existing)

        version_before_update(cortex, new)
        stored = cortex.store.call_args[0][0]
        assert "versioned" not in stored.metadata

    def test_increments_seq(self):
        existing = _mock_memory(narrative="old")
        new = _mock_memory(narrative="new")
        cortex = _mock_cortex_with_existing(existing)
        conn = cortex._conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = (3,)  # 3 existing versions

        result = version_before_update(cortex, new)
        assert result == "TEST_V_v004"


class TestVersionHistory:
    def test_returns_empty_on_no_versions(self):
        cortex = MagicMock()
        conn = MagicMock()
        cortex._conn.return_value.__enter__.return_value = conn
        cortex._conn.return_value.__exit__.return_value = False
        conn.execute.return_value.fetchall.return_value = []

        history = get_version_history(cortex, "TEST_V")
        assert history == []
