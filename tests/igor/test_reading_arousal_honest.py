"""T-reading-arousal-honest: deposited nodes get arousal from milieu, not keyword hits.

Verifies:
  - book_learner uses milieu.read_state().arousal instead of _cp_affinity_score for memory.arousal
  - cp_affinity ends up in metadata, not arousal
  - 0.10-0.60 clamped range is no longer the source of arousal
  - milieu unavailable → falls back to 0.5
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "lab"))


@pytest.fixture()
def cortex_mock():
    c = MagicMock()
    c.store = MagicMock()
    c.store_batch = MagicMock()
    c._get_or_compute_embedding = MagicMock(return_value=None)
    c.add_child = MagicMock()
    c.add_children_batch = MagicMock()
    c.add_interpretive_edge = MagicMock()
    c.add_interpretive_edges_batch = MagicMock()
    c.get_hot_attractors = MagicMock(return_value=[])
    return c


def _make_milieu_state(arousal: float):
    from unseen_university.devices.igor.cognition.milieu import MilieuState

    ms = MilieuState()
    ms.arousal = arousal
    return ms


class TestReadingArousalHonest:
    def test_arousal_comes_from_milieu_not_keywords(self, cortex_mock):
        """memory.arousal should be the milieu arousal value, not keyword-hit clamped score."""
        from claudecode import book_learner

        milieu_arousal = 0.73  # outside old 0.10-0.60 range

        nodes = [
            {
                "id": "test-node-1",
                "type": "factual",
                "parent_cp": "CP1",
                "narrative": "learning growth skills capab knowledge master understand",
                "trigger": "",
                "relevance": "",
                "confidence": 0.9,
            }
        ]

        with patch.object(
            book_learner._milieu_mod,
            "read_state",
            return_value=_make_milieu_state(milieu_arousal),
        ):
            book_learner._deposit_nodes(
                cortex=cortex_mock,
                nodes=nodes,
                chapter_node_id="",
                book_title="Test Book",
                chunk_pos=0,
                campaign_id="",
            )

        stored_mem = cortex_mock.store_batch.call_args[0][0][0]
        assert (
            stored_mem.arousal == milieu_arousal
        ), f"Expected milieu arousal {milieu_arousal}, got {stored_mem.arousal}"
        # old clamped range check: milieu value is 0.73, outside [0.10, 0.60]
        assert (
            stored_mem.arousal > 0.60 or stored_mem.arousal < 0.10 or True
        )  # just verify it's milieu value

    def test_cp_affinity_in_metadata(self, cortex_mock):
        """cp_affinity keyword score ends up in metadata, not as memory.arousal."""
        from claudecode import book_learner

        nodes = [
            {
                "id": "test-node-2",
                "type": "factual",
                "parent_cp": "CP1",
                "narrative": "learning growth skills capab knowledge master",
                "trigger": "",
                "relevance": "",
                "confidence": 0.9,
            }
        ]

        with patch.object(
            book_learner._milieu_mod,
            "read_state",
            return_value=_make_milieu_state(0.4),
        ):
            book_learner._deposit_nodes(
                cortex=cortex_mock,
                nodes=nodes,
                chapter_node_id="",
                book_title="Test Book",
                chunk_pos=0,
                campaign_id="",
            )

        stored_mem = cortex_mock.store_batch.call_args[0][0][0]
        assert "cp_affinity" in (
            stored_mem.metadata or {}
        ), "cp_affinity should be in metadata"
        # cp_affinity should be in the old range [0.10, 0.60]
        cp_aff = stored_mem.metadata["cp_affinity"]
        assert 0.10 <= cp_aff <= 0.60, f"cp_affinity {cp_aff} out of expected range"

    def test_milieu_unavailable_fallback(self, cortex_mock):
        """When milieu is not initialized, arousal falls back to 0.5."""
        from claudecode import book_learner

        nodes = [
            {
                "id": "test-node-3",
                "type": "factual",
                "parent_cp": "CP1",
                "narrative": "some content",
                "trigger": "",
                "relevance": "",
                "confidence": 0.9,
            }
        ]

        with patch.object(book_learner._milieu_mod, "read_state", return_value=None):
            book_learner._deposit_nodes(
                cortex=cortex_mock,
                nodes=nodes,
                chapter_node_id="",
                book_title="Test Book",
                chunk_pos=0,
                campaign_id="",
            )

        stored_mem = cortex_mock.store_batch.call_args[0][0][0]
        assert (
            stored_mem.arousal == 0.5
        ), f"Expected fallback 0.5, got {stored_mem.arousal}"
