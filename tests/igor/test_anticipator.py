"""tests for cognition/anticipator.py — T-anticipation-primitive slice 1.

Covers:
  Anticipation dataclass shape
  Anticipator.predict — initial 0.0 + confidence growth + per-type isolation
  Anticipator.register_outcome — RPE math + weight update reflected in next predict
  AnticipationBus — push / top_k ranking / settle / active_count
  Module singletons — get_anticipator / get_bus / reset_for_test
"""

from __future__ import annotations

import pytest

from unseen_university.devices.igor.cognition.anticipator import (
    Anticipation,
    Anticipator,
    AnticipationBus,
    get_anticipator,
    get_bus,
    reset_for_test,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_for_test()
    yield
    reset_for_test()


# ── Anticipation dataclass ─────────────────────────────────────────────────


class TestAnticipationDataclass:
    def test_carries_required_fields(self):
        ant = Anticipation(
            referent_id="T-test",
            referent_type="ticket",
            predicted_delta=0.5,
            confidence=0.8,
        )
        assert ant.referent_id == "T-test"
        assert ant.referent_type == "ticket"
        assert ant.predicted_delta == 0.5
        assert ant.confidence == 0.8
        assert ant.created_at > 0
        assert ant.id.startswith("ant-")

    def test_unique_ids_across_instances(self):
        a = Anticipation("r1", "t", 0.0, 0.0)
        b = Anticipation("r1", "t", 0.0, 0.0)
        assert a.id != b.id


# ── Anticipator predictor ───────────────────────────────────────────────────


class TestAnticipatorPredict:
    def test_initial_prediction_is_zero_with_zero_confidence(self):
        ap = Anticipator()
        ant = ap.predict("T-x", "ticket")
        assert ant.predicted_delta == 0.0
        assert ant.confidence == 0.0

    def test_register_outcome_returns_signed_rpe(self):
        ap = Anticipator()
        ant = ap.predict("T-x", "ticket")
        rpe = ap.register_outcome(ant, actual_delta=0.7)
        # First sample: predicted=0, actual=0.7 → RPE = 0.7
        assert rpe == pytest.approx(0.7)

    def test_register_outcome_updates_future_predictions(self):
        ap = Anticipator()
        ant1 = ap.predict("T-x", "ticket")
        ap.register_outcome(ant1, actual_delta=1.0)
        ant2 = ap.predict("T-y", "ticket")
        # Mean of [1.0] = 1.0; confidence after 1 sample = 1 - 1/2 = 0.5
        assert ant2.predicted_delta == pytest.approx(1.0)
        assert ant2.confidence == pytest.approx(0.5)

    def test_register_outcome_rolling_mean(self):
        ap = Anticipator()
        for actual in (1.0, 0.0, -1.0, 2.0):
            ant = ap.predict("rid", "ticket")
            ap.register_outcome(ant, actual_delta=actual)
        # Mean of [1, 0, -1, 2] = 0.5; confidence after 4 = 1 - 1/5 = 0.8
        ant = ap.predict("rid", "ticket")
        assert ant.predicted_delta == pytest.approx(0.5)
        assert ant.confidence == pytest.approx(0.8)

    def test_referent_types_are_isolated(self):
        ap = Anticipator()
        ap.register_outcome(ap.predict("a", "ticket"), 1.0)
        ap.register_outcome(ap.predict("b", "pursuit"), -1.0)
        # ticket-type predictions should not see the pursuit-type sample
        assert ap.predict("c", "ticket").predicted_delta == pytest.approx(1.0)
        assert ap.predict("d", "pursuit").predicted_delta == pytest.approx(-1.0)
        # A fresh type starts at zero
        assert ap.predict("e", "workflow").predicted_delta == 0.0


# ── AnticipationBus ────────────────────────────────────────────────────────


class TestAnticipationBus:
    def test_push_then_active_count(self):
        bus = AnticipationBus()
        bus.push(Anticipation("r", "t", 0.5, 0.5))
        bus.push(Anticipation("r2", "t", 0.5, 0.5))
        assert bus.active_count() == 2

    def test_top_k_ranks_by_delta_times_confidence(self):
        bus = AnticipationBus()
        bus.push(Anticipation("low", "t", 0.1, 0.9))  # 0.09
        bus.push(Anticipation("high", "t", 0.9, 0.9))  # 0.81
        bus.push(Anticipation("mid", "t", 0.5, 0.5))  # 0.25
        ranked = bus.top_k(k=3)
        assert [a.referent_id for a in ranked] == ["high", "mid", "low"]

    def test_top_k_returns_at_most_k(self):
        bus = AnticipationBus()
        for i in range(5):
            bus.push(Anticipation(f"r{i}", "t", float(i), 1.0))
        assert len(bus.top_k(k=2)) == 2

    def test_settle_removes_and_returns(self):
        bus = AnticipationBus()
        ant = Anticipation("r", "t", 0.5, 0.5)
        bus.push(ant)
        settled = bus.settle(ant.id)
        assert settled is ant
        assert bus.active_count() == 0

    def test_settle_unknown_returns_none(self):
        bus = AnticipationBus()
        assert bus.settle("ant-nonexistent") is None

    def test_push_is_idempotent_on_id(self):
        bus = AnticipationBus()
        ant = Anticipation("r", "t", 0.5, 0.5)
        bus.push(ant)
        bus.push(ant)  # same id — should not duplicate
        assert bus.active_count() == 1


# ── Module-level singletons ────────────────────────────────────────────────


class TestSingletons:
    def test_get_anticipator_returns_same_instance(self):
        a = get_anticipator()
        b = get_anticipator()
        assert a is b

    def test_get_bus_returns_same_instance(self):
        a = get_bus()
        b = get_bus()
        assert a is b

    def test_reset_for_test_clears_singletons(self):
        a = get_anticipator()
        reset_for_test()
        b = get_anticipator()
        assert a is not b


# ── Integration: predict → push → top_k → settle → register_outcome ────────


class TestEndToEndLoop:
    def test_full_loop(self):
        """Producer-side: predict + push. Consumer-side: top_k. Resolution:
        settle + register_outcome → RPE flows back into predictor weights."""
        ap = get_anticipator()
        bus = get_bus()

        # Produce: predict for one referent and push onto bus
        ant = ap.predict("T-cool-feature", "ticket")
        bus.push(ant)

        # Consume: top_k surfaces it (only one active)
        top = bus.top_k(k=1)
        assert top == [ant]

        # Resolve: settle + register an unexpectedly-high actual delta
        settled = bus.settle(ant.id)
        assert settled is ant
        rpe = ap.register_outcome(settled, actual_delta=0.8)
        assert rpe == pytest.approx(0.8)  # predicted 0.0, got 0.8

        # The predictor learned — next prediction for ticket-type is 0.8
        next_ant = ap.predict("T-other", "ticket")
        assert next_ant.predicted_delta == pytest.approx(0.8)
        # Confidence rises after 1 sample
        assert next_ant.confidence == pytest.approx(0.5)
