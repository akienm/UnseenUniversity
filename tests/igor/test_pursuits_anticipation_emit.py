"""tests for T-anticipation-slice2-pursuit-emit.

Pursuit adoption emits an Anticipation onto the bus; resolution
(completion or abandonment) settles the bus and feeds register_outcome
so the Anticipator's predictor learns.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition import anticipator, pursuits


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Reset both registries + enable pursuits so spawn() takes the real path."""
    anticipator.reset_for_test()
    pursuits.registry().clear()
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "true")
    yield
    anticipator.reset_for_test()
    pursuits.registry().clear()


def _always_false(_state: dict) -> bool:
    return False


def _always_true(_state: dict) -> bool:
    return True


class TestSpawnEmits:
    def test_spawn_pushes_anticipation_onto_bus(self):
        bus = anticipator.get_bus()
        assert bus.active_count() == 0
        p = pursuits.spawn(
            name="test-pursuit",
            entry_stimulus={"trigger": "test"},
            goal_facia=_always_false,
        )
        assert bus.active_count() == 1
        assert p.anticipation_id is not None
        assert p.anticipation_id.startswith("ant-")

    def test_spawn_pushes_anticipation_with_pursuit_referent_type(self):
        pursuits.spawn(
            name="t",
            entry_stimulus={},
            goal_facia=_always_false,
        )
        top = anticipator.get_bus().top_k(k=1)
        assert len(top) == 1
        assert top[0].referent_type == "pursuit"

    def test_disabled_pursuits_do_not_push(self, monkeypatch):
        monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "false")
        bus = anticipator.get_bus()
        p = pursuits.spawn(
            name="disabled-pursuit",
            entry_stimulus={},
            goal_facia=_always_false,
        )
        assert p.status == "disabled"
        assert p.anticipation_id is None
        assert bus.active_count() == 0


class TestEvaluateCompletionSettles:
    def test_completion_settles_bus_and_feeds_register_outcome(self):
        bus = anticipator.get_bus()
        ap = anticipator.get_anticipator()
        p = pursuits.spawn(
            name="will-complete",
            entry_stimulus={},
            goal_facia=_always_true,
        )
        assert bus.active_count() == 1
        result = p.evaluate_completion(state={})
        assert result == "completed"
        # Bus emptied
        assert bus.active_count() == 0
        # Predictor learned from actual_delta=1.0 → next pursuit-type
        # prediction has predicted_delta=1.0
        future = ap.predict("other", "pursuit")
        assert future.predicted_delta == pytest.approx(1.0)

    def test_abandonment_settles_bus_with_negative_delta(self):
        bus = anticipator.get_bus()
        ap = anticipator.get_anticipator()
        p = pursuits.spawn(
            name="will-abandon",
            entry_stimulus={},
            goal_facia=_always_false,
        )
        assert bus.active_count() == 1
        result = p.evaluate_completion(state={})
        assert result == "abandoned"
        assert bus.active_count() == 0
        future = ap.predict("other", "pursuit")
        assert future.predicted_delta == pytest.approx(-0.5)

    def test_settle_unknown_anticipation_does_not_raise(self):
        """If something settled the anticipation between spawn and
        evaluate_completion, the second settle is a silent no-op."""
        bus = anticipator.get_bus()
        p = pursuits.spawn(
            name="prematurely-settled",
            entry_stimulus={},
            goal_facia=_always_true,
        )
        # Pre-settle the bus
        bus.settle(p.anticipation_id)
        # Should not raise
        result = p.evaluate_completion(state={})
        assert result == "completed"

    def test_pursuit_without_anticipation_id_does_not_raise(self):
        """Direct Pursuit construction (test fixtures) skipping spawn() has
        anticipation_id=None; settle should be a no-op."""
        p = pursuits.Pursuit(
            id="x",
            name="test",
            entry_stimulus={},
            goal_facia=_always_true,
            commitment_ts=0.0,
        )
        assert p.anticipation_id is None
        result = p.evaluate_completion(state={})
        assert result == "completed"  # no exception


class TestRollingMeanAcrossMultiplePursuits:
    def test_predictor_averages_across_pursuit_outcomes(self):
        ap = anticipator.get_anticipator()
        # Three pursuits: 2 complete, 1 abandons
        for _ in range(2):
            p = pursuits.spawn("p", {}, _always_true)
            p.evaluate_completion(state={})
        p = pursuits.spawn("p", {}, _always_false)
        p.evaluate_completion(state={})
        # Mean of [1.0, 1.0, -0.5] = 0.5
        future = ap.predict("next", "pursuit")
        assert future.predicted_delta == pytest.approx(0.5)
