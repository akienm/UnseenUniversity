"""
Tests for T-308: hebbian_bridge — word graph / memory graph bridge.

Covers:
- wg_boost_search: predicted-word hits boost candidate scores
- wg_boost_search: no-ops when disabled or word_graph is None
- record_retrieval_boost: calls reinforce_text for high-importance memories
- record_retrieval_boost: skips memories with importance < 0.7
- wg_predict_for_activation: returns union of predicted words from activated nodes
- wg_predict_for_activation: no-op when disabled or empty activations
- cortex.search(): passes word_graph through when provided (smoke test)
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_mock_memory(
    mem_id: str,
    narrative: str = "test narrative",
    importance: float = 0.5,
    relevance_score: float = 0.3,
):
    m = MagicMock()
    m.id = mem_id
    m.narrative = narrative
    m.importance = importance
    m.confidence = importance
    m.relevance_score = relevance_score
    m.memory_type = MagicMock()
    return m


_DEFAULT_PREDICTIONS = [("memory", 0.8), ("graph", 0.6), ("igor", 0.5)]


def _make_mock_wg(predictions=None):
    """Return a mock word graph whose predict_next() returns given predictions."""
    wg = MagicMock()
    wg.predict_next.return_value = (
        _DEFAULT_PREDICTIONS if predictions is None else predictions
    )
    return wg


# ── wg_boost_search ───────────────────────────────────────────────────────────


class TestWgBoostSearch(unittest.TestCase):
    def setUp(self):
        # Force enable the bridge for all tests in this class
        os.environ["IGOR_HEBBIAN_BRIDGE"] = "true"
        # Reload module so _ENABLED re-evaluates
        import importlib
        import devices.igor.cognition.coactivation_counter as hb

        importlib.reload(hb)
        self.hb = hb

    def tearDown(self):
        os.environ.pop("IGOR_HEBBIAN_BRIDGE", None)

    def test_predicted_words_boost_matching_candidate(self):
        """Candidate narrative containing predicted words gets a positive boost."""
        wg = _make_mock_wg([("graph", 0.9), ("memory", 0.8)])
        candidates = [
            _make_mock_memory("M1", narrative="the memory graph traversal"),
            _make_mock_memory("M2", narrative="weather forecast tomorrow"),
        ]
        boosts = self.hb.wg_boost_search(wg, "query text", candidates)
        self.assertIn("M1", boosts, "M1 narrative contains predicted words")
        self.assertNotIn("M2", boosts, "M2 has no predicted-word overlap")
        self.assertGreater(boosts["M1"], 0.0)
        self.assertLessEqual(boosts["M1"], 0.10)

    def test_no_predictions_returns_empty(self):
        """Empty predict_next result → no boosts."""
        wg = _make_mock_wg([])
        candidates = [_make_mock_memory("M1", narrative="memory graph")]
        boosts = self.hb.wg_boost_search(wg, "query", candidates)
        self.assertEqual(boosts, {})

    def test_returns_empty_when_word_graph_none(self):
        boosts = self.hb.wg_boost_search(None, "query", [_make_mock_memory("M1")])
        self.assertEqual(boosts, {})

    def test_returns_empty_when_candidates_empty(self):
        boosts = self.hb.wg_boost_search(_make_mock_wg(), "query", [])
        self.assertEqual(boosts, {})

    def test_disabled_returns_empty(self):
        """When env gate is off, always returns empty."""
        import importlib
        import devices.igor.cognition.coactivation_counter as hb

        os.environ["IGOR_HEBBIAN_BRIDGE"] = "false"
        importlib.reload(hb)
        wg = _make_mock_wg([("memory", 0.9)])
        boosts = hb.wg_boost_search(
            wg, "query", [_make_mock_memory("M1", narrative="memory")]
        )
        self.assertEqual(boosts, {})
        # Restore
        os.environ["IGOR_HEBBIAN_BRIDGE"] = "true"
        importlib.reload(hb)
        self.hb = hb


# ── record_retrieval_boost ────────────────────────────────────────────────────


class TestRecordRetrievalBoost(unittest.TestCase):
    def setUp(self):
        os.environ["IGOR_HEBBIAN_BRIDGE"] = "true"
        import importlib
        import devices.igor.cognition.coactivation_counter as hb

        importlib.reload(hb)
        self.hb = hb

    def tearDown(self):
        os.environ.pop("IGOR_HEBBIAN_BRIDGE", None)

    def test_high_importance_calls_reinforce_text(self):
        """Memory with importance >= 0.7 triggers word graph reinforcement."""
        wg = MagicMock()
        m = _make_mock_memory(
            "M1", narrative="spreading activation traversal", importance=0.8
        )
        self.hb.record_retrieval_boost(wg, m, arousal=0.5)
        wg.reinforce_text.assert_called_once()
        args, kwargs = wg.reinforce_text.call_args
        self.assertIsInstance(args[0], str)  # key_terms text
        self.assertGreater(kwargs.get("boost", args[1] if len(args) > 1 else 0), 0)

    def test_low_importance_skips_reinforce(self):
        """Memory with importance < 0.7 → reinforce_text not called."""
        wg = MagicMock()
        m = _make_mock_memory("M1", narrative="spreading activation", importance=0.5)
        self.hb.record_retrieval_boost(wg, m, arousal=0.8)
        wg.reinforce_text.assert_not_called()

    def test_boost_proportional_to_arousal(self):
        """Higher arousal → higher boost value passed to reinforce_text."""
        wg_low = MagicMock()
        wg_high = MagicMock()
        m = _make_mock_memory(
            "M1", narrative="memory graph traversal node", importance=0.9
        )

        self.hb.record_retrieval_boost(wg_low, m, arousal=0.2)
        self.hb.record_retrieval_boost(wg_high, m, arousal=0.9)

        boost_low = wg_low.reinforce_text.call_args[1].get(
            "boost",
            (
                wg_low.reinforce_text.call_args[0][1]
                if len(wg_low.reinforce_text.call_args[0]) > 1
                else 0
            ),
        )
        boost_high = wg_high.reinforce_text.call_args[1].get(
            "boost",
            (
                wg_high.reinforce_text.call_args[0][1]
                if len(wg_high.reinforce_text.call_args[0]) > 1
                else 0
            ),
        )
        self.assertGreater(boost_high, boost_low)

    def test_boost_capped_at_arsl_boost_cap(self):
        """Boost never exceeds _ARSL_BOOST_CAP (0.15) even at arousal=1.0."""
        wg = MagicMock()
        m = _make_mock_memory("M1", narrative="memory graph importance", importance=1.0)
        self.hb.record_retrieval_boost(wg, m, arousal=1.0)
        boost = wg.reinforce_text.call_args[1].get(
            "boost",
            (
                wg.reinforce_text.call_args[0][1]
                if len(wg.reinforce_text.call_args[0]) > 1
                else 1.0
            ),
        )
        self.assertLessEqual(boost, self.hb._ARSL_BOOST_CAP)

    def test_noop_when_word_graph_none(self):
        """No error, no call when word_graph is None."""
        m = _make_mock_memory("M1", importance=0.9)
        self.hb.record_retrieval_boost(None, m, arousal=0.5)  # should not raise

    def test_noop_when_memory_none(self):
        wg = MagicMock()
        self.hb.record_retrieval_boost(wg, None, arousal=0.5)
        wg.reinforce_text.assert_not_called()


# ── wg_predict_for_activation ─────────────────────────────────────────────────


class TestWgPredictForActivation(unittest.TestCase):
    def setUp(self):
        os.environ["IGOR_HEBBIAN_BRIDGE"] = "true"
        import importlib
        import devices.igor.cognition.coactivation_counter as hb

        importlib.reload(hb)
        self.hb = hb

    def tearDown(self):
        os.environ.pop("IGOR_HEBBIAN_BRIDGE", None)

    def test_returns_predicted_words_union(self):
        """Predictions from all activated nodes are unioned."""
        wg = MagicMock()
        wg.predict_next.side_effect = [
            [("memory", 0.8), ("graph", 0.6)],
            [("arousal", 0.7), ("graph", 0.5)],
        ]
        activated = [
            _make_mock_memory("M1", narrative="the memory graph"),
            _make_mock_memory("M2", narrative="arousal state"),
        ]
        result = self.hb.wg_predict_for_activation(wg, activated)
        self.assertIn("memory", result)
        self.assertIn("graph", result)
        self.assertIn("arousal", result)

    def test_returns_empty_when_word_graph_none(self):
        result = self.hb.wg_predict_for_activation(None, [_make_mock_memory("M1")])
        self.assertEqual(result, set())

    def test_returns_empty_when_no_activations(self):
        result = self.hb.wg_predict_for_activation(_make_mock_wg(), [])
        self.assertEqual(result, set())

    def test_caps_at_five_nodes(self):
        """At most 5 nodes queried regardless of activated list length."""
        wg = MagicMock()
        wg.predict_next.return_value = [("word", 0.5)]
        activated = [_make_mock_memory(f"M{i}") for i in range(10)]
        self.hb.wg_predict_for_activation(wg, activated)
        self.assertLessEqual(wg.predict_next.call_count, 5)


# ── cortex integration smoke test ─────────────────────────────────────────────


class TestCortexSearchWordGraphParam(unittest.TestCase):
    """Smoke test: cortex.search() accepts word_graph kwarg without error."""

    def test_search_accepts_word_graph_param(self):
        """cortex.search(word_graph=None) signature is valid."""
        import inspect
        from devices.igor.memory.cortex import Cortex

        sig = inspect.signature(Cortex.search)
        self.assertIn("word_graph", sig.parameters)

    def test_spread_activation_accepts_word_graph_param(self):
        """_spread_activation(word_graph=None) signature is valid."""
        import inspect
        from devices.igor.memory.cortex import Cortex

        sig = inspect.signature(Cortex._spread_activation)
        self.assertIn("word_graph", sig.parameters)


if __name__ == "__main__":
    unittest.main()
