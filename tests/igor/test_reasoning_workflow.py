"""
test_reasoning_workflow.py — T-reasoning-workflow-primitive

Tests for the Workflow base class, Conversation/Utterance dataclasses,
WorkflowRecorder, the run_workflow runner, and Workflow A (experiment
design) as the first concrete implementation.

All tests use a scripted PeerAdvisor — no real LLM required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.decision_blob import ProposedExperiment  # noqa: E402
from unseen_university.devices.igor.cognition.reasoning_workflow import (  # noqa: E402
    Conversation,
    PeerAdvisor,
    Speaker,
    TransitionRecord,
    Workflow,
    WorkflowA_ExperimentDesign,
    WorkflowComplete,
    WorkflowRecorder,
    WorkflowUtterance,
    _extract_field,
    run_workflow,
)

# ── Scripted peer for tests ──────────────────────────────────────────────────


class ScriptedPeer(PeerAdvisor):
    """Returns scripted responses in order."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[Conversation] = []

    def respond(self, conversation: Conversation) -> str:
        self.calls.append(conversation)
        if not self._responses:
            return "(scripted peer ran out of responses)"
        return self._responses.pop(0)


# ── Dataclasses ──────────────────────────────────────────────────────────────


def test_utterance_speaker_coerced_from_string():
    u = WorkflowUtterance(speaker="igor", content="hi")  # type: ignore
    assert u.speaker == Speaker.IGOR


def test_utterance_has_timestamp_default():
    u = WorkflowUtterance(speaker=Speaker.IGOR, content="hi")
    assert u.timestamp  # non-empty iso string


def test_conversation_tracks_utterances():
    c = Conversation(workflow_name="wf_test")
    c.add(WorkflowUtterance(speaker=Speaker.IGOR, content="one"))
    c.add(WorkflowUtterance(speaker=Speaker.PEER, content="two"))
    assert c.length() == 2
    assert c.last_igor().content == "one"
    assert c.last_peer().content == "two"


def test_conversation_last_igor_before_peer():
    c = Conversation(workflow_name="w")
    c.add(WorkflowUtterance(speaker=Speaker.IGOR, content="q1"))
    c.add(WorkflowUtterance(speaker=Speaker.PEER, content="a1"))
    c.add(WorkflowUtterance(speaker=Speaker.IGOR, content="q2"))
    assert c.last_igor().content == "q2"


def test_conversation_has_unique_id():
    c1 = Conversation(workflow_name="w")
    c2 = Conversation(workflow_name="w")
    assert c1.conversation_id != c2.conversation_id


def test_workflow_complete_default_not_aborted():
    wc = WorkflowComplete(output="x", reason="done")
    assert wc.aborted is False


# ── Base Workflow raises NotImplementedError ─────────────────────────────────


def test_base_workflow_opening_raises():
    wf = Workflow()
    with pytest.raises(NotImplementedError):
        wf.opening_utterance({})


def test_base_workflow_next_raises():
    wf = Workflow()
    conv = Conversation(workflow_name="w")
    u = WorkflowUtterance(speaker=Speaker.PEER, content="x")
    with pytest.raises(NotImplementedError):
        wf.next_utterance(conv, u)


def test_base_workflow_output_raises():
    wf = Workflow()
    conv = Conversation(workflow_name="w")
    with pytest.raises(NotImplementedError):
        wf.output_struct(conv)


# ── WorkflowA: opening ───────────────────────────────────────────────────────


def test_workflow_a_opening_names_uncertainty():
    wf = WorkflowA_ExperimentDesign()
    u = wf.opening_utterance({"uncertainty": "does cortex.search hit on widgets"})
    assert u.speaker == Speaker.IGOR
    assert "does cortex.search hit on widgets" in u.content
    assert u.metadata.get("opening") is True
    assert u.expected_response_shape == "probe + expected observation"


def test_workflow_a_opening_includes_current_state_and_tried():
    wf = WorkflowA_ExperimentDesign()
    u = wf.opening_utterance(
        {
            "uncertainty": "X",
            "current_state": "weights are 0.5",
            "what_i_tried": "searched 'widget'",
        }
    )
    assert "weights are 0.5" in u.content
    assert "searched 'widget'" in u.content


def test_workflow_a_opening_handles_missing_fields():
    wf = WorkflowA_ExperimentDesign()
    u = wf.opening_utterance({})
    # Should not crash; placeholders used
    assert "something unclear" in u.content


# ── WorkflowA: single-pass completion when peer gives both fields ────────────


def test_workflow_a_completes_when_peer_gives_probe_and_expected():
    wf = WorkflowA_ExperimentDesign()
    situation = {"uncertainty": "does X produce Y"}
    peer = ScriptedPeer(
        [
            "Probe: call tool_foo(bar=1). Expected: result contains 'OK' when X holds.",
        ]
    )
    run = run_workflow(wf, situation, peer)
    assert run.complete.aborted is False
    assert isinstance(run.complete.output, ProposedExperiment)
    assert run.complete.output.hypothesis
    assert "tool_foo" in run.complete.output.probe
    assert run.complete.output.expected_observation
    assert "OK" in run.complete.output.expected_observation


def test_workflow_a_asks_for_probe_when_missing():
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(
        [
            # First response: no probe, just expected
            "Expected: you should see memories with tag 'widget'.",
            # Second response: fills in the probe
            "Probe: call cortex.search('widget'). Expected: memories with tag 'widget'.",
        ]
    )
    run = run_workflow(wf, {"uncertainty": "X"}, peer)
    assert run.complete.aborted is False
    assert isinstance(run.complete.output, ProposedExperiment)
    # At least 2 peer turns consumed before completion
    assert len(peer.calls) == 2


def test_workflow_a_asks_for_expected_when_missing():
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(
        [
            "Probe: call cortex.search('x').",
            "Probe: call cortex.search('x'). Expected: at least one result.",
        ]
    )
    run = run_workflow(wf, {"uncertainty": "X"}, peer)
    assert run.complete.aborted is False
    assert isinstance(run.complete.output, ProposedExperiment)


def test_workflow_a_aborts_on_max_turns():
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(["I need more context."] * 20)
    run = run_workflow(wf, {"uncertainty": "X"}, peer, max_turns=6)
    assert run.complete.aborted is True
    assert "max_turns" in run.complete.reason


def test_workflow_a_conversation_preserved_in_run():
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(
        [
            "Probe: call tool_x. Expected: returns Y.",
        ]
    )
    run = run_workflow(wf, {"uncertainty": "Z"}, peer)
    # At least 2 utterances (opening + peer response)
    assert run.conversation.length() >= 2
    assert run.conversation.utterances[0].speaker == Speaker.IGOR


# ── WorkflowRecorder ─────────────────────────────────────────────────────────


def test_recorder_captures_transitions_during_run():
    wf = WorkflowA_ExperimentDesign()
    recorder = WorkflowRecorder()
    peer = ScriptedPeer(
        [
            "Probe: tool_x. Expected: Y output.",
        ]
    )
    run_workflow(wf, {"uncertainty": "Z"}, peer, recorder=recorder)
    recs = recorder.records()
    # At least one transition recorded
    assert len(recs) >= 1
    r = recs[0]
    assert r.workflow_name == "workflow_a_experiment_design"
    assert r.transition_index == 0


def test_recorder_matched_flag_reflects_expected_shape():
    wf = WorkflowA_ExperimentDesign()
    recorder = WorkflowRecorder()
    # Peer response must contain ALL content tokens from the expected
    # shape ("probe + expected observation" → probe, expected, observation)
    # for matched=True (T-experiment-predictor-primitive token rule).
    peer = ScriptedPeer(
        [
            "Probe: tool_x. Expected: Y observation counts as data.",
        ]
    )
    run_workflow(wf, {"uncertainty": "Z"}, peer, recorder=recorder)
    recs = recorder.records()
    first = recs[0]
    assert first.expected_peer_move == "probe + expected observation"
    assert first.matched is True


def test_recorder_matched_flag_false_on_incomplete_shape():
    """If the peer response misses any content token from the expected
    shape, matched should be False."""
    wf = WorkflowA_ExperimentDesign()
    recorder = WorkflowRecorder()
    peer = ScriptedPeer(
        [
            # Missing 'observation' token — matches neither as substring
            # nor via the per-token rule
            "Probe: tool_x. Expected: Y.",
        ]
    )
    run_workflow(wf, {"uncertainty": "Z"}, peer, recorder=recorder)
    first = recorder.records()[0]
    assert first.matched is False


def test_recorder_clear_empties():
    rec = WorkflowRecorder()
    rec.record(
        TransitionRecord(
            workflow_name="w",
            conversation_id="c",
            transition_index=0,
            igor_state={},
            expected_peer_move="x",
            actual_peer_move="y",
            matched=False,
        )
    )
    rec.clear()
    assert len(rec.records()) == 0


def test_recorder_persist_writes_via_cortex_store():
    rec = WorkflowRecorder()
    rec.record(
        TransitionRecord(
            workflow_name="w",
            conversation_id="c",
            transition_index=0,
            igor_state={"key": "val"},
            expected_peer_move="probe + expected observation",
            actual_peer_move="Probe: x. Expected: y.",
            matched=True,
        )
    )
    cortex = MagicMock()
    cortex.store.side_effect = lambda m, **_: m
    n = rec.persist(cortex)
    assert n == 1
    assert cortex.store.called
    stored = cortex.store.call_args.args[0]
    assert stored.metadata["type"] == "workflow_transition"
    assert stored.metadata["matched"] is True


def test_recorder_persist_empty_returns_zero():
    rec = WorkflowRecorder()
    cortex = MagicMock()
    assert rec.persist(cortex) == 0
    assert not cortex.store.called


def test_recorder_persist_continues_on_individual_failure():
    rec = WorkflowRecorder()
    for i in range(3):
        rec.record(
            TransitionRecord(
                workflow_name="w",
                conversation_id="c",
                transition_index=i,
                igor_state={},
                expected_peer_move="",
                actual_peer_move="",
                matched=False,
            )
        )
    cortex = MagicMock()

    call_count = {"n": 0}

    def _flaky_store(mem, **_):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("middle one fails")
        return mem

    cortex.store.side_effect = _flaky_store
    n = rec.persist(cortex)
    # 2 successful + 1 failed → 2 persisted
    assert n == 2


# ── _extract_field helper ────────────────────────────────────────────────────


def test_extract_field_finds_marker():
    result = _extract_field("Probe: call tool_x(y=1)", ["probe:"])
    assert result == "call tool_x(y=1)"


def test_extract_field_case_insensitive():
    result = _extract_field("PROBE: tool_x", ["probe:"])
    assert result == "tool_x"


def test_extract_field_stops_at_newline():
    result = _extract_field(
        "Probe: tool_x\nExpected: Y",
        ["probe:"],
    )
    assert result == "tool_x"


def test_extract_field_tries_alternatives():
    result = _extract_field("Test: do thing", ["probe:", "test:"])
    assert result == "do thing"


def test_extract_field_missing_returns_none():
    result = _extract_field("nothing here", ["probe:"])
    assert result is None


def test_extract_field_empty_input():
    assert _extract_field("", ["probe:"]) is None
    assert _extract_field(None, ["probe:"]) is None  # type: ignore


# ── Output extraction from completed conversation ──────────────────────────


def test_workflow_a_output_struct_rebuilds_from_conversation():
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(
        [
            "Probe: search('thing'). Expected: 3 results.",
        ]
    )
    run = run_workflow(wf, {"uncertainty": "is thing there"}, peer)
    # Rebuild via output_struct (should match what WorkflowComplete carried)
    rebuilt = wf.output_struct(run.conversation)
    assert isinstance(rebuilt, ProposedExperiment)
    assert "thing" in rebuilt.probe.lower() or "search" in rebuilt.probe.lower()


# ── Regression: decision_blob.ProposedExperiment compatibility ──────────────


def test_workflow_a_output_is_proposed_experiment_compatible():
    """The output struct must match decision_blob.ProposedExperiment
    shape so it can be handed off to from_proposed bridge in
    experiment.py."""
    wf = WorkflowA_ExperimentDesign()
    peer = ScriptedPeer(
        [
            "Probe: check X. Expected: Y state.",
        ]
    )
    run = run_workflow(wf, {"uncertainty": "does X"}, peer)
    output = run.complete.output
    # Has all required ProposedExperiment fields
    assert hasattr(output, "hypothesis")
    assert hasattr(output, "probe")
    assert hasattr(output, "expected_observation")
    assert hasattr(output, "cost_estimate")
