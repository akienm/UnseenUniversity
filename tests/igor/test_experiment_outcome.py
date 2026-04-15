"""
test_experiment_outcome.py — T-experiment-primitive-outcome-feedback (#456 sub)

Unit tests split into:
  - derive_update: pure logic, no cortex needed
  - apply_outcome: lifecycle + persistence, mocked cortex
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.experiment import (  # noqa: E402
    Experiment,
    ExperimentStatus,
    Hypothesis,
    Observation,
    Outcome,
    Probe,
    ProbeKind,
)
from wild_igor.igor.cognition.experiment_outcome import (  # noqa: E402
    HEBBIAN_DELTA_MATCH,
    INHIBITOR_DELTA_TOOL_MISMATCH,
    apply_outcome,
    derive_update,
    feedback_tick,
)


def _make_observed(
    outcome: Outcome = Outcome.MATCH,
    probe_kind: ProbeKind = ProbeKind.MEMORY_QUERY,
    probe_target: str = "word_graph",
    hypothesis_source: str = "substrate",
    cp_constraints: dict | None = None,
) -> Experiment:
    exp = Experiment(
        hypothesis=Hypothesis(
            statement="searching for X surfaces relevant memories",
            source=hypothesis_source,
            confidence=0.5,
            cp_constraints=cp_constraints or {},
        ),
        probe=Probe(
            kind=probe_kind,
            target=probe_target,
            expected_shape="at least one row",
        ),
    )
    exp.advance(ExperimentStatus.RUNNING)
    exp.record_observation(Observation(outcome=outcome, data={}))
    return exp


def _make_mock_cortex():
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    conn.fetchone.return_value = None
    conn.fetchall.return_value = []
    cortex.twm_push.return_value = 1

    # cortex.store returns the memory it was given, with the id intact
    def _store(mem, **_kwargs):
        return mem

    cortex.store.side_effect = _store
    return cortex, conn


# ── derive_update: pure logic ────────────────────────────────────────────────


def test_derive_update_match_strengthens_edge():
    exp = _make_observed(outcome=Outcome.MATCH)
    update = derive_update(exp)
    assert len(update.trail_edge_changes) == 1
    edge = update.trail_edge_changes[0]
    assert edge["delta"] == HEBBIAN_DELTA_MATCH
    assert edge["from"] == "substrate"
    assert edge["to"] == "word_graph"
    assert "strengthened" in update.reason


def test_derive_update_mismatch_no_hebbian_strengthen():
    exp = _make_observed(outcome=Outcome.MISMATCH)
    update = derive_update(exp)
    assert update.trail_edge_changes == []


def test_derive_update_inconclusive_still_records_memory():
    exp = _make_observed(outcome=Outcome.INCONCLUSIVE)
    update = derive_update(exp)
    # Memory accretion records "we tried this and learned it didn't help"
    assert len(update.memory_accretions) == 1
    assert update.memory_accretions[0].startswith("PENDING:")


def test_derive_update_tool_mismatch_bumps_inhibitor():
    exp = _make_observed(
        outcome=Outcome.MISMATCH,
        probe_kind=ProbeKind.TOOL_CALL,
        probe_target="some_tool",
    )
    update = derive_update(exp)
    assert update.inhibitor_weight_deltas == {
        "tool:some_tool": INHIBITOR_DELTA_TOOL_MISMATCH
    }
    assert "tool inhibitor" in update.reason


def test_derive_update_memory_query_mismatch_does_not_bump_inhibitor():
    """Only tool_call mismatches bump inhibitors — memory queries don't have
    a tool to penalize."""
    exp = _make_observed(
        outcome=Outcome.MISMATCH,
        probe_kind=ProbeKind.MEMORY_QUERY,
    )
    update = derive_update(exp)
    assert update.inhibitor_weight_deltas == {}


def test_derive_update_goal_match_advances_goal():
    exp = _make_observed(
        outcome=Outcome.MATCH,
        cp_constraints={"goal_id": "PR_GOAL_TEST"},
    )
    update = derive_update(exp)
    assert len(update.goal_state_transitions) == 1
    assert update.goal_state_transitions[0]["goal_id"] == "PR_GOAL_TEST"
    assert update.goal_state_transitions[0]["to"] == "in_progress"


def test_derive_update_goal_mismatch_blocks_goal():
    exp = _make_observed(
        outcome=Outcome.MISMATCH,
        cp_constraints={"goal_id": "PR_GOAL_TEST"},
    )
    update = derive_update(exp)
    assert update.goal_state_transitions[0]["to"] == "blocked"


def test_derive_update_no_goal_id_skips_transitions():
    exp = _make_observed(outcome=Outcome.MATCH)
    update = derive_update(exp)
    assert update.goal_state_transitions == []


def test_derive_update_reason_required_and_populated():
    """CP3: every Update.reason must be non-empty."""
    exp = _make_observed()
    update = derive_update(exp)
    assert update.reason
    assert "outcome=" in update.reason


def test_derive_update_requires_observation():
    exp = Experiment(
        hypothesis=Hypothesis(statement="x", source="s"),
        probe=Probe(kind=ProbeKind.MEMORY_QUERY, target="t"),
    )
    with pytest.raises(ValueError, match="requires an Observation"):
        derive_update(exp)


# ── apply_outcome: lifecycle + persistence ───────────────────────────────────


def test_apply_outcome_transitions_to_updated():
    cortex, conn = _make_mock_cortex()
    exp = _make_observed()

    result = apply_outcome(cortex, exp)
    assert result.status == ExperimentStatus.UPDATED
    assert result.update is not None
    assert result.update.reason


def test_apply_outcome_persists_via_update():
    cortex, conn = _make_mock_cortex()
    exp = _make_observed()
    apply_outcome(cortex, exp)

    # An UPDATE statement against experiment_queue ran
    update_calls = [
        call
        for call in conn.execute.call_args_list
        if "UPDATE experiment_queue" in call.args[0]
    ]
    assert len(update_calls) >= 1


def test_apply_outcome_pushes_committed_to_twm():
    cortex, _ = _make_mock_cortex()
    exp = _make_observed()
    apply_outcome(cortex, exp)

    push = cortex.twm_push.call_args
    metadata = push.kwargs["metadata"]
    assert metadata["cp1_provisional"] is False
    assert metadata["type"] == "experiment_updated"


def test_apply_outcome_match_inserts_trail_edge():
    cortex, conn = _make_mock_cortex()
    exp = _make_observed(outcome=Outcome.MATCH)
    apply_outcome(cortex, exp)

    edge_inserts = [
        call
        for call in conn.execute.call_args_list
        if "INSERT INTO interpretive_edges" in call.args[0]
    ]
    assert len(edge_inserts) == 1


def test_apply_outcome_mismatch_skips_trail_edge_insert():
    cortex, conn = _make_mock_cortex()
    exp = _make_observed(outcome=Outcome.MISMATCH)
    apply_outcome(cortex, exp)

    edge_inserts = [
        call
        for call in conn.execute.call_args_list
        if "INSERT INTO interpretive_edges" in call.args[0]
    ]
    assert edge_inserts == []


def test_apply_outcome_deposits_episodic_memory():
    cortex, _ = _make_mock_cortex()
    exp = _make_observed()
    apply_outcome(cortex, exp)

    # cortex.store was called at least once with a Memory object
    assert cortex.store.called
    stored = cortex.store.call_args.args[0]
    assert stored.metadata["type"] == "experiment_outcome"
    assert stored.metadata["experiment_id"] == exp.experiment_id


def test_apply_outcome_inhibitor_delta_deposits_proc_memory():
    cortex, _ = _make_mock_cortex()
    exp = _make_observed(
        outcome=Outcome.MISMATCH, probe_kind=ProbeKind.TOOL_CALL, probe_target="t"
    )
    apply_outcome(cortex, exp)
    # Two stores: episodic outcome + procedural inhibitor delta
    assert cortex.store.call_count == 2
    stored_types = [c.args[0].metadata.get("type") for c in cortex.store.call_args_list]
    assert "inhibitor_delta" in stored_types


def test_apply_outcome_rejects_non_observed():
    cortex, _ = _make_mock_cortex()
    exp = Experiment(
        hypothesis=Hypothesis(statement="x", source="s"),
        probe=Probe(kind=ProbeKind.MEMORY_QUERY, target="t"),
    )
    with pytest.raises(ValueError, match="requires status=OBSERVED"):
        apply_outcome(cortex, exp)


def test_apply_outcome_rejects_already_updated():
    cortex, _ = _make_mock_cortex()
    exp = _make_observed()
    apply_outcome(cortex, exp)
    with pytest.raises(ValueError, match="requires status=OBSERVED"):
        apply_outcome(cortex, exp)


def test_apply_outcome_replaces_pending_memory_id_on_success():
    """After deposit, the placeholder PENDING:<id> should be replaced with the
    real stored memory id."""
    cortex, _ = _make_mock_cortex()

    def _store_with_real_id(mem, **_kwargs):
        mem.id = "REAL_MEM_ID"
        return mem

    cortex.store.side_effect = _store_with_real_id

    exp = _make_observed()
    result = apply_outcome(cortex, exp)
    assert "REAL_MEM_ID" in result.update.memory_accretions
    assert not any(a.startswith("PENDING:") for a in result.update.memory_accretions)


# ── feedback_tick (queue scan) ───────────────────────────────────────────────


def test_feedback_tick_empty_queue_returns_none():
    cortex, _ = _make_mock_cortex()
    assert feedback_tick(cortex) is None


def test_feedback_tick_picks_observed_and_runs():
    cortex, conn = _make_mock_cortex()
    exp = _make_observed()
    conn.fetchone.return_value = (exp.to_json(),)

    result = feedback_tick(cortex)
    assert result is not None
    assert result.status == ExperimentStatus.UPDATED
