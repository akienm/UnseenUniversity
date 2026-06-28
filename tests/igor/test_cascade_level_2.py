"""
test_cascade_level_2.py — T-cascade-level-2-interpretive

Tests for Level2InterpretiveTraversal — BFS via interpretive edges
from query-relevant seeds.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.experiment_cascade import (  # noqa: E402
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    Level2InterpretiveTraversal,
)


def _mock_memory(id_: str, narrative: str = "test"):
    m = MagicMock()
    m.id = id_
    m.narrative = narrative
    m.metadata = {}
    return m


class TestLevel2InterpretiveTraversal:
    def test_name(self):
        assert Level2InterpretiveTraversal.name == "level_2_interpretive_traversal"

    def test_match_when_traversal_finds_new_memories(self):
        cortex = MagicMock()
        seed = _mock_memory("SEED_1")
        new_mem = _mock_memory("NEW_1", "discovered via edges")
        cortex.search.return_value = [seed]
        cortex.interpretive_traverse.return_value = [seed, new_mem]

        level = Level2InterpretiveTraversal()
        situation = CascadeSituation(query="what is igor?")
        result = level.try_probe(cortex, situation)

        assert result.status == CascadeStatus.MATCHED
        assert len(result.data) == 1
        assert result.data[0].id == "NEW_1"
        cortex.interpretive_traverse.assert_called_once()

    def test_exhausted_when_traversal_adds_nothing(self):
        cortex = MagicMock()
        seed = _mock_memory("SEED_1")
        cortex.search.return_value = [seed]
        cortex.interpretive_traverse.return_value = [seed]

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="test"))

        assert result.status == CascadeStatus.EXHAUSTED
        assert "nothing beyond" in result.reason

    def test_exhausted_when_no_seeds(self):
        cortex = MagicMock()
        cortex.search.return_value = []

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="unknown"))

        assert result.status == CascadeStatus.EXHAUSTED
        assert "no seed" in result.reason
        cortex.interpretive_traverse.assert_not_called()

    def test_exhausted_on_search_exception(self):
        cortex = MagicMock()
        cortex.search.side_effect = RuntimeError("db down")

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="test"))

        assert result.status == CascadeStatus.EXHAUSTED
        assert "RuntimeError" in result.reason

    def test_exhausted_on_traverse_exception(self):
        cortex = MagicMock()
        cortex.search.return_value = [_mock_memory("SEED")]
        cortex.interpretive_traverse.side_effect = ValueError("bad edge")

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="test"))

        assert result.status == CascadeStatus.EXHAUSTED
        assert "ValueError" in result.reason

    def test_multiple_seeds_multiple_new(self):
        cortex = MagicMock()
        seeds = [_mock_memory(f"S{i}") for i in range(3)]
        new_mems = [_mock_memory(f"N{i}") for i in range(4)]
        cortex.search.return_value = seeds
        cortex.interpretive_traverse.return_value = seeds + new_mems

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="deep question"))

        assert result.status == CascadeStatus.MATCHED
        assert len(result.data) == 4
        assert "4 new memories from 3 seeds" in result.reason

    def test_experiment_attached_on_match(self):
        cortex = MagicMock()
        cortex.search.return_value = [_mock_memory("S")]
        cortex.interpretive_traverse.return_value = [
            _mock_memory("S"),
            _mock_memory("N"),
        ]

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="test"))

        assert result.experiment is not None
        assert result.experiment.observation is not None

    def test_experiment_attached_on_exhaustion(self):
        cortex = MagicMock()
        cortex.search.return_value = [_mock_memory("S")]
        cortex.interpretive_traverse.return_value = [_mock_memory("S")]

        level = Level2InterpretiveTraversal()
        result = level.try_probe(cortex, CascadeSituation(query="test"))

        assert result.experiment is not None

    def test_search_called_with_limit_3(self):
        cortex = MagicMock()
        cortex.search.return_value = []

        level = Level2InterpretiveTraversal()
        level.try_probe(cortex, CascadeSituation(query="test query"))

        cortex.search.assert_called_once_with("test query", limit=3)

    def test_traverse_called_with_seed_ids(self):
        cortex = MagicMock()
        cortex.search.return_value = [_mock_memory("A"), _mock_memory("B")]
        cortex.interpretive_traverse.return_value = []

        level = Level2InterpretiveTraversal()
        level.try_probe(cortex, CascadeSituation(query="q"))

        cortex.interpretive_traverse.assert_called_once_with(
            ["A", "B"], max_depth=3, min_weight=0.1
        )

    def test_registered_in_default_cascade(self):
        """Level 2 should be a concrete Level2InterpretiveTraversal in
        the default cascade, not a stub."""
        cortex = MagicMock()
        cortex.search.return_value = []
        cortex.twm_push.return_value = 1
        from unseen_university.devices.igor.cognition.experiment_cascade import build_default_cascade

        cascade = build_default_cascade(cortex)
        level_names = [l.name for l in cascade._levels]
        assert "level_2_interpretive_traversal" in level_names
        l2 = [l for l in cascade._levels if l.name == "level_2_interpretive_traversal"][
            0
        ]
        assert isinstance(l2, Level2InterpretiveTraversal)
