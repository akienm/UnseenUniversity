"""
test_experiment.py — T-experiment-primitive-schema

Unit tests for the experiment primitive data model. Same shape as
test_decision_blob.py — dataclass validation + enum coercion +
lifecycle state machine + serialization roundtrip + cross-module
bridge.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.experiment import (  # noqa: E402
    Experiment,
    ExperimentStatus,
    Hypothesis,
    Observation,
    Outcome,
    Probe,
    ProbeKind,
    Update,
    from_proposed,
)

# ── Hypothesis ───────────────────────────────────────────────────────────────


def test_hypothesis_construction():
    h = Hypothesis(
        statement="input X should produce outcome Y",
        source="substrate",
        confidence=0.6,
    )
    assert h.statement == "input X should produce outcome Y"
    assert h.source == "substrate"
    assert h.confidence == 0.6
    assert h.cp_constraints == {}


def test_hypothesis_requires_non_empty_statement():
    with pytest.raises(ValueError, match="CP3: causal story required"):
        Hypothesis(statement="   ", source="substrate")


def test_hypothesis_confidence_range():
    with pytest.raises(ValueError, match="must be in"):
        Hypothesis(statement="x", source="s", confidence=1.5)
    with pytest.raises(ValueError, match="must be in"):
        Hypothesis(statement="x", source="s", confidence=-0.1)


# ── Probe ────────────────────────────────────────────────────────────────────


def test_probe_construction():
    p = Probe(
        kind=ProbeKind.TOOL_CALL,
        target="cortex.search",
        payload={"query": "word graph"},
        expected_shape="at least 1 FACTUAL result",
    )
    assert p.kind == ProbeKind.TOOL_CALL
    assert p.target == "cortex.search"
    assert p.expected_shape == "at least 1 FACTUAL result"


def test_probe_kind_coercion_from_string():
    p = Probe(kind="memory_query", target="mem_123")  # type: ignore
    assert p.kind == ProbeKind.MEMORY_QUERY


def test_probe_requires_target():
    with pytest.raises(ValueError, match="target must be non-empty"):
        Probe(kind=ProbeKind.TOOL_CALL, target="")


# ── Observation ──────────────────────────────────────────────────────────────


def test_observation_construction():
    o = Observation(
        outcome=Outcome.MATCH,
        data={"rows": 3},
        cost={"latency_ms": 45},
    )
    assert o.outcome == Outcome.MATCH
    assert o.data["rows"] == 3
    assert o.cost["latency_ms"] == 45


def test_observation_outcome_coercion():
    o = Observation(outcome="mismatch")  # type: ignore
    assert o.outcome == Outcome.MISMATCH


def test_observation_inconclusive_is_firstclass():
    """CP1: inconclusive is a valid outcome, not a failure mode."""
    o = Observation(outcome=Outcome.INCONCLUSIVE)
    assert o.outcome == Outcome.INCONCLUSIVE


# ── Update ───────────────────────────────────────────────────────────────────


def test_update_construction():
    u = Update(
        trail_edge_changes=[{"from": "A", "to": "B", "delta": 0.05}],
        memory_accretions=["mem_001"],
        inhibitor_weight_deltas={"coherence_inhibitor": -0.02},
        goal_state_transitions=[
            {"goal_id": "g1", "from": "in_progress", "to": "completed"}
        ],
        reason="observation matched hypothesis; strengthening the prior",
    )
    assert u.trail_edge_changes[0]["delta"] == 0.05
    assert "mem_001" in u.memory_accretions
    assert u.inhibitor_weight_deltas["coherence_inhibitor"] == -0.02
    assert u.reason.startswith("observation matched")


# ── Experiment construction + auto-id ───────────────────────────────────────


def _make_experiment() -> Experiment:
    return Experiment(
        hypothesis=Hypothesis(
            statement="x causes y", source="substrate", confidence=0.5
        ),
        probe=Probe(kind=ProbeKind.MEMORY_QUERY, target="mem_abc"),
    )


def test_experiment_auto_id_format():
    """Format: yyyymmdd.hhmmssuuuuuu.xxxxxxx per D256 uniform rule."""
    e = _make_experiment()
    assert re.match(r"^\d{8}\.\d{12}\.[a-f0-9]{7}$", e.experiment_id)


def test_experiment_ids_unique_within_microsecond():
    ids = {_make_experiment().experiment_id for _ in range(100)}
    assert len(ids) == 100


def test_experiment_default_status_is_proposed():
    e = _make_experiment()
    assert e.status == ExperimentStatus.PROPOSED
    assert e.observation is None
    assert e.update is None


# ── Lifecycle transitions ────────────────────────────────────────────────────


def test_lifecycle_happy_path():
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    assert e.status == ExperimentStatus.RUNNING

    e.record_observation(Observation(outcome=Outcome.MATCH, data={"rows": 2}))
    assert e.status == ExperimentStatus.OBSERVED
    assert e.observation is not None

    e.apply_update(Update(reason="match confirmed the hypothesis"))
    assert e.status == ExperimentStatus.UPDATED
    assert e.update is not None


def test_cannot_record_observation_from_proposed():
    """record_observation requires RUNNING — can't skip straight from PROPOSED."""
    e = _make_experiment()
    with pytest.raises(ValueError, match="requires status=RUNNING"):
        e.record_observation(Observation(outcome=Outcome.MATCH))


def test_cannot_apply_update_from_running():
    """apply_update requires OBSERVED — can't skip straight from RUNNING."""
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    with pytest.raises(ValueError, match="requires status=OBSERVED"):
        e.apply_update(Update(reason="premature"))


def test_update_requires_non_empty_reason():
    """CP3: the 'why' of the update is required."""
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    e.record_observation(Observation(outcome=Outcome.MATCH))
    with pytest.raises(ValueError, match="Update.reason must be non-empty"):
        e.apply_update(Update(reason=""))


def test_terminal_states_cannot_transition():
    """UPDATED and ABORTED are terminal — no further transitions allowed."""
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    e.record_observation(Observation(outcome=Outcome.MATCH))
    e.apply_update(Update(reason="done"))
    with pytest.raises(ValueError, match="Invalid transition"):
        e.advance(ExperimentStatus.RUNNING)


def test_abort_from_any_non_terminal_state():
    e = _make_experiment()
    e.abort("cost exceeded")
    assert e.status == ExperimentStatus.ABORTED

    e2 = _make_experiment()
    e2.advance(ExperimentStatus.RUNNING)
    e2.abort("scheduler timeout")
    assert e2.status == ExperimentStatus.ABORTED


def test_abort_from_terminal_rejected():
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    e.record_observation(Observation(outcome=Outcome.MISMATCH))
    e.apply_update(Update(reason="learned from mismatch"))
    with pytest.raises(ValueError, match="cannot abort from terminal state"):
        e.abort()


def test_invalid_transition_reports_allowed():
    e = _make_experiment()
    with pytest.raises(ValueError, match="Allowed:.*running"):
        e.advance(ExperimentStatus.OBSERVED)  # skipping RUNNING


# ── Serialization ────────────────────────────────────────────────────────────


def test_to_dict_enum_serialization():
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    e.record_observation(Observation(outcome=Outcome.PARTIAL, data={"n": 1}))
    e.apply_update(Update(reason="partial match, weak signal"))
    d = e.to_dict()
    assert d["status"] == "updated"  # enum value
    assert d["probe"]["kind"] == "memory_query"
    assert d["observation"]["outcome"] == "partial"


def test_roundtrip_dict():
    e = _make_experiment()
    e.advance(ExperimentStatus.RUNNING)
    e.record_observation(Observation(outcome=Outcome.MATCH, data={"rows": 5}))
    e.apply_update(
        Update(
            trail_edge_changes=[{"from": "a", "to": "b", "delta": 0.1}],
            reason="strengthened edge on confirmation",
        )
    )
    back = Experiment.from_dict(e.to_dict())
    assert back.status == ExperimentStatus.UPDATED
    assert back.probe.kind == ProbeKind.MEMORY_QUERY
    assert back.observation.outcome == Outcome.MATCH
    assert back.observation.data["rows"] == 5
    assert back.update.trail_edge_changes[0]["delta"] == 0.1


def test_roundtrip_json():
    e = _make_experiment()
    text = e.to_json()
    back = Experiment.from_json(text)
    assert back.hypothesis.statement == "x causes y"
    assert back.probe.target == "mem_abc"
    assert back.status == ExperimentStatus.PROPOSED


def test_parent_blob_id_preserved():
    e = Experiment(
        hypothesis=Hypothesis(statement="test", source="llm"),
        probe=Probe(kind=ProbeKind.TOOL_CALL, target="foo"),
        parent_blob_id="20260414.200000123456.abc1234",
    )
    back = Experiment.from_dict(e.to_dict())
    assert back.parent_blob_id == "20260414.200000123456.abc1234"


# ── Bridge from decision_blob.ProposedExperiment ─────────────────────────────


def test_from_proposed_bridge():
    from devices.igor.cognition.decision_blob import ProposedExperiment

    proposed = ProposedExperiment(
        hypothesis="calling tool X with input Y produces Z",
        probe="tool_X(Y)",
        expected_observation="Z-shaped output",
        cost_estimate="cheap, ~50ms",
    )
    experiment = from_proposed(
        proposed,
        source="reasoning_llm",
        confidence=0.6,
        probe_kind=ProbeKind.TOOL_CALL,
        probe_target="tool_X",
        probe_payload={"input": "Y"},
        parent_blob_id="20260414.200000123456.abc1234",
    )
    assert experiment.status == ExperimentStatus.PROPOSED
    assert experiment.hypothesis.statement == "calling tool X with input Y produces Z"
    assert experiment.hypothesis.source == "reasoning_llm"
    assert experiment.hypothesis.confidence == 0.6
    assert experiment.probe.kind == ProbeKind.TOOL_CALL
    assert experiment.probe.target == "tool_X"
    assert experiment.probe.payload == {"input": "Y"}
    assert experiment.probe.expected_shape == "Z-shaped output"
    assert experiment.probe.cost_estimate == "cheap, ~50ms"
    assert experiment.parent_blob_id == "20260414.200000123456.abc1234"
