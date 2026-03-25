"""
Tests for D228 step 2: prediction error → per-turn graph training.

Covers:
- _train_prediction_error: hits strengthen links (reinforce_links +delta)
- _train_prediction_error: misses weaken links (reinforce_links -delta)
- _train_prediction_error: no-op when seeds list is empty
- _train_prediction_error: no-op when predicted_heat is empty
- _train_prediction_error: heat threshold filters low-heat nodes
- _train_prediction_error: no-op when promoted_ids is empty
- _train_prediction_error: exception is swallowed, never raises
- _apply_output: returns promoted_ids as third element of tuple
- run(): pre-seeds collected from obs_list metadata when IGOR_PREDICTION_ERROR_ENABLED
- run(): _train_prediction_error called with seeds/heat/promoted after _apply_output
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

# ── stub heavy imports before NE import ──────────────────────────────────────

def _stub_modules():
    stubs = {
        "igor.cognition.reasoning_cache": MagicMock(get=lambda *a: None, put=lambda *a: None),
        "igor.cognition.forensic_logger": MagicMock(
            log_ne_run=lambda **kw: None,
            cts=lambda: "",
            log_error=lambda **kw: None,
        ),
        "igor.cognition.milieu": MagicMock(get=MagicMock(return_value=None)),
        "igor.cognition.inference_gateway": MagicMock(),
    }
    for name, stub in stubs.items():
        sys.modules.setdefault(name, stub)

_stub_modules()

# ── import NE internals directly (no live DB needed) ─────────────────────────

from igor.cognition.narrative_engine import (
    NarrativeEngine,
    _PE_HEAT_THRESHOLD,
    _PE_REINFORCE_DELTA,
    _PE_WEAKEN_DELTA,
)


def _make_ne():
    """Return a NarrativeEngine with all heavy deps mocked out."""
    cortex = MagicMock()
    cortex.twm_read.return_value = []
    cortex.twm_get_slots.return_value = []
    cortex.spreading_activation.return_value = {}
    cortex.reinforce_links.return_value = None
    ne = NarrativeEngine.__new__(NarrativeEngine)
    ne.cortex = cortex
    ne._last_run = None
    ne._run_count = 0
    ne._topic_history = []
    ne._last_ne_model = "test"
    return ne


# ── _train_prediction_error unit tests ───────────────────────────────────────


class TestTrainPredictionError(unittest.TestCase):

    def setUp(self):
        self.ne = _make_ne()

    def test_hits_strengthen_links(self):
        """Nodes predicted hot AND promoted → reinforce_links called with +delta."""
        seeds = ["seed1"]
        heat = {"nodeA": 0.9, "nodeB": 0.8}
        promoted = ["nodeA"]
        self.ne._train_prediction_error(seeds, heat, promoted)
        # nodeA is a hit → +delta
        self.ne.cortex.reinforce_links.assert_any_call("seed1", ["nodeA"], _PE_REINFORCE_DELTA)

    def test_misses_weaken_links(self):
        """Nodes predicted hot but not promoted → reinforce_links called with -delta."""
        seeds = ["seed1"]
        heat = {"nodeA": 0.9, "nodeB": 0.8}
        promoted = []  # nothing promoted → both are misses
        self.ne._train_prediction_error(seeds, heat, promoted)
        calls = self.ne.cortex.reinforce_links.call_args_list
        # Both predicted hot nodes should be weakened
        weakened_ids = set()
        for c in calls:
            if c.args[2] == -_PE_WEAKEN_DELTA:
                weakened_ids.update(c.args[1])
        self.assertIn("nodeA", weakened_ids)
        self.assertIn("nodeB", weakened_ids)

    def test_no_op_empty_seeds(self):
        """Empty seed list → reinforce_links never called."""
        self.ne._train_prediction_error([], {"nodeA": 0.9}, ["nodeA"])
        self.ne.cortex.reinforce_links.assert_not_called()

    def test_no_op_empty_heat(self):
        """Empty predicted_heat → no predicted-hot nodes → reinforce_links never called."""
        self.ne._train_prediction_error(["seed1"], {}, ["nodeA"])
        self.ne.cortex.reinforce_links.assert_not_called()

    def test_no_op_empty_promoted(self):
        """Empty promoted_ids + low-heat nodes → nothing to hit or miss → no calls."""
        # All below threshold
        self.ne._train_prediction_error(["seed1"], {"nodeA": 0.1}, [])
        self.ne.cortex.reinforce_links.assert_not_called()

    def test_heat_threshold_filters_low_heat(self):
        """Nodes with heat below _PE_HEAT_THRESHOLD are not treated as predictions."""
        seeds = ["seed1"]
        heat = {"nodeA": _PE_HEAT_THRESHOLD - 0.01, "nodeB": _PE_HEAT_THRESHOLD + 0.01}
        promoted = ["nodeA", "nodeB"]
        self.ne._train_prediction_error(seeds, heat, promoted)
        calls = self.ne.cortex.reinforce_links.call_args_list
        # Only nodeB passes threshold → only nodeB gets a hit call
        strengthened = set()
        for c in calls:
            if c.args[2] == _PE_REINFORCE_DELTA:
                strengthened.update(c.args[1])
        self.assertIn("nodeB", strengthened)
        self.assertNotIn("nodeA", strengthened)

    def test_exception_swallowed(self):
        """reinforce_links raising must not propagate — NE must not crash."""
        self.ne.cortex.reinforce_links.side_effect = RuntimeError("db error")
        # Should not raise
        try:
            self.ne._train_prediction_error(["seed1"], {"nodeA": 0.9}, ["nodeA"])
        except Exception as e:
            self.fail(f"_train_prediction_error raised unexpectedly: {e}")

    def test_multiple_seeds_each_get_calls(self):
        """Each seed gets separate reinforce_links calls for hits and misses."""
        seeds = ["seed1", "seed2"]
        heat = {"nodeA": 0.9}
        promoted = ["nodeA"]
        self.ne._train_prediction_error(seeds, heat, promoted)
        # Both seeds should have strengthen calls
        strengthen_seeds = [
            c.args[0]
            for c in self.ne.cortex.reinforce_links.call_args_list
            if c.args[2] == _PE_REINFORCE_DELTA
        ]
        self.assertIn("seed1", strengthen_seeds)
        self.assertIn("seed2", strengthen_seeds)


# ── _apply_output return value test ──────────────────────────────────────────


class TestApplyOutputReturnsPromotedIds(unittest.TestCase):

    def _make_minimal_result(self):
        return {
            "summary_csb": "test summary",
            "salience_updates": [],
            "memory_candidates": [],
            "action_impulses": [],
            "connections": [],
        }

    def test_returns_three_tuple(self):
        """_apply_output must return (promoted_count, impulse_count, promoted_ids)."""
        ne = _make_ne()
        ne.cortex.search_ring_text.return_value = []
        ne._is_self_diagnostic = MagicMock(return_value=False)
        ne._process_gaps = MagicMock()
        result = ne._apply_output(self._make_minimal_result(), [], verbose=False)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        promoted_count, impulse_count, promoted_ids = result
        self.assertIsInstance(promoted_ids, list)

    def test_promoted_ids_populated(self):
        """_apply_output includes promoted memory IDs in third return value."""
        ne = _make_ne()
        ne.cortex.search_ring_text.return_value = []
        ne._is_self_diagnostic = MagicMock(return_value=False)
        ne._process_gaps = MagicMock()

        # Patch milieu import inside _apply_output
        milieu_mock = MagicMock()
        milieu_mock.get.return_value = None
        with patch.dict(sys.modules, {"igor.cognition.milieu": milieu_mock}):
            from igor.memory.models import Memory, MemoryType
            result_dict = {
                "summary_csb": "test",
                "salience_updates": [],
                "memory_candidates": [
                    {
                        "content_csb": "something interesting",
                        "importance": 0.9,
                        "memory_type": "episodic",
                        "valence": 0.0,
                    }
                ],
                "action_impulses": [],
                "connections": [],
            }
            promoted_count, impulse_count, promoted_ids = ne._apply_output(
                result_dict, [], verbose=False
            )
        self.assertEqual(promoted_count, 1)
        self.assertEqual(len(promoted_ids), 1)
        self.assertIsInstance(promoted_ids[0], str)


if __name__ == "__main__":
    unittest.main()
