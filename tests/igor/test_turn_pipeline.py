"""
test_turn_pipeline.py — T-turn-pipeline-module

Tests for the TurnPipeline conductor. Uses mocked cortex + fake
cascades + scripted PeerAdvisor. Never touches main.py or the real
LLM path.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.decision_blob import (  # noqa: E402
    DecisionBlob,
    Intent,
    ProposedExperiment,
    Provenance as DBProvenance,
)
from unseen_university.devices.igor.cognition.experiment_cascade import (  # noqa: E402
    BaseCascadeLevel,
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    ExperimentCascade,
)
from unseen_university.devices.igor.cognition.prompt_contexts import PromptContext  # noqa: E402
from unseen_university.devices.igor.cognition.reasoning_workflow import (  # noqa: E402
    Conversation,
    PeerAdvisor,
)
from unseen_university.devices.igor.cognition.turn_pipeline import (  # noqa: E402
    PathStep,
    TurnPipeline,
    TurnResult,
    VoiceProducer,
    _blob_from_cascade_match,
    _blob_from_workflow_output,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_cortex():
    cortex = MagicMock()
    cortex.search.return_value = []
    cortex.twm_push.return_value = 1
    return cortex


def _situation(query: str = "find the goal tree") -> CascadeSituation:
    return CascadeSituation(query=query)


class _MatchingLevel(BaseCascadeLevel):
    """Level that always MATCHES — for testing the match path."""

    name = "test_matching_level"

    def try_probe(self, cortex, situation):
        return CascadeResult(
            status=CascadeStatus.MATCHED,
            level_name=self.name,
            data=["matched_result"],
            reason="test match",
        )


class _EscalatingLevel(BaseCascadeLevel):
    """Level that always ESCALATES — for testing the workflow path."""

    name = "test_escalating_level"

    def try_probe(self, cortex, situation):
        return CascadeResult(
            status=CascadeStatus.ESCALATE,
            level_name=self.name,
            data={"hand_off": situation.query},
            reason="test escalate",
        )


class _ExhaustingLevel(BaseCascadeLevel):
    """Level that always EXHAUSTS — for testing the exhausted path."""

    name = "test_exhausting_level"

    def try_probe(self, cortex, situation):
        return CascadeResult(
            status=CascadeStatus.EXHAUSTED,
            level_name=self.name,
            reason="nothing to try",
        )


class _ScriptedPeer(PeerAdvisor):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def respond(self, conversation: Conversation) -> str:
        if not self._responses:
            return "(out of responses)"
        return self._responses.pop(0)


def _make_cascade(cortex, level: BaseCascadeLevel) -> ExperimentCascade:
    c = ExperimentCascade(cortex)
    c.register(level)
    return c


# ── VoiceProducer stub behavior ─────────────────────────────────────────────


def _tiny_ctx() -> PromptContext:
    return PromptContext(
        phase="voice",
        system_text="stub",
        sections={},
    )


def test_voice_producer_renders_selected_action():
    vp = VoiceProducer()
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action="here is the answer",
        confidence=0.9,
        provenance=DBProvenance(maker="substrate", inputs=["x"]),
    )
    text = vp.produce(blob, _tiny_ctx())
    assert "here is the answer" in text


def test_voice_producer_commits_without_apology_on_selected_action():
    """Confident-in-process: even at lower confidence, the voice
    commits to the action without adding an apologetic hedge."""
    vp = VoiceProducer()
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action="do the thing",
        confidence=0.4,
        provenance=DBProvenance(maker="substrate", inputs=["x"]),
    )
    text = vp.produce(blob, _tiny_ctx())
    assert "do the thing" in text
    # No apologetic hedges — the stance is certain-of-current-best-guess
    assert "provisional" not in text.lower()
    assert "uncertain" not in text.lower()


def test_voice_producer_current_best_guess_on_proposed_experiment():
    """With a ProposedExperiment on the blob, voice renders as
    'current best guess + how we're testing it' — confident in
    process, not hedged."""
    vp = VoiceProducer()
    proposed = ProposedExperiment(
        hypothesis="X causes Y",
        probe="call tool_foo(bar=1)",
        expected_observation="returns OK",
    )
    blob = DecisionBlob(
        intent=Intent.EXPERIMENT,
        hypothesis="X causes Y",
        confidence=0.5,
        proposed_experiment=proposed,
        provenance=DBProvenance(maker="reasoning_llm", inputs=["workflow"]),
    )
    text = vp.produce(blob, _tiny_ctx())
    assert "current best guess" in text.lower()
    assert "tool_foo" in text
    assert "OK" in text
    # No apology
    assert "uncertain" not in text.lower()
    assert "not confident" not in text.lower()


def test_voice_producer_best_guess_fallthrough():
    vp = VoiceProducer()
    blob = DecisionBlob(
        intent=Intent.DEFER,
        hypothesis="X might be Y",
        confidence=0.3,
        provenance=DBProvenance(maker="substrate", inputs=["x"]),
    )
    text = vp.produce(blob, _tiny_ctx())
    assert "current best guess" in text.lower()
    assert "X might be Y" in text


def test_voice_producer_no_hypothesis_fallthrough():
    vp = VoiceProducer()
    blob = DecisionBlob(
        intent=Intent.DEFER,
        confidence=0.3,
        provenance=DBProvenance(maker="substrate", inputs=["x"]),
    )
    text = vp.produce(blob, _tiny_ctx())
    assert "still working" in text.lower()


# ── TurnPipeline: cascade match path ────────────────────────────────────────


def test_pipeline_cascade_match_produces_voice_reply():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _MatchingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    result = pipeline.run_turn(_situation("find the goal tree"))

    assert isinstance(result, TurnResult)
    assert result.reply_text
    assert result.cascade_result.status == CascadeStatus.MATCHED
    assert result.decision_blob is not None
    assert result.decision_blob.intent == Intent.ANSWER
    assert result.voice_context is not None
    assert result.voice_context.phase == "voice"
    # Path trace records the stages
    steps = [e.step for e in result.path_trace]
    assert PathStep.CASCADE in steps
    assert PathStep.DECISION_BLOB in steps
    assert PathStep.CAN_COMMIT in steps
    assert PathStep.VOICE_CONTEXT in steps
    assert PathStep.VOICE_PRODUCTION in steps


def test_pipeline_cascade_match_does_not_run_workflow():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _MatchingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    peer = _ScriptedPeer(["should not be called"])
    result = pipeline.run_turn(_situation(), peer_advisor=peer)

    assert result.workflow_run is None
    # Peer should still have its full script — never consulted
    assert peer._responses == ["should not be called"]


# ── TurnPipeline: cascade escalation path ───────────────────────────────────


def test_pipeline_cascade_escalate_runs_workflow_with_peer():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _EscalatingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    # Scripted peer provides probe + expected in one response
    peer = _ScriptedPeer(
        [
            "Probe: run cortex.search('igor dev'). Expected: at least one facia result.",
        ]
    )
    result = pipeline.run_turn(_situation("find igor dev"), peer_advisor=peer)

    assert result.workflow_run is not None
    assert result.workflow_run.complete.aborted is False
    assert result.decision_blob is not None
    assert result.decision_blob.proposed_experiment is not None
    assert "cortex.search" in result.decision_blob.proposed_experiment.probe
    # Reply should be confident-in-process: 'current best guess + how I test it'
    assert "current best guess" in result.reply_text.lower()
    assert "testing it" in result.reply_text.lower()


def test_pipeline_cascade_escalate_without_peer_returns_honest_gap():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _EscalatingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    result = pipeline.run_turn(_situation())
    assert "stuck" in result.reply_text.lower()
    assert "peer" in result.reply_text.lower()
    # No workflow run, no blob, no voice context
    assert result.workflow_run is None
    assert result.decision_blob is None


def test_pipeline_workflow_abort_produces_honest_reply():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _EscalatingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    # Peer keeps giving incomplete responses → workflow will abort
    # after max_turns. Use enough responses to drive through the loop.
    peer = _ScriptedPeer(["I need more context"] * 20)
    result = pipeline.run_turn(_situation(), peer_advisor=peer)

    assert result.workflow_run is not None
    assert result.workflow_run.complete.aborted is True
    # Honest reply explains the gap, doesn't fake an answer
    assert (
        "didn't reach" in result.reply_text.lower()
        or "honestly" in result.reply_text.lower()
    )


# ── TurnPipeline: cascade exhausted path ────────────────────────────────────


def test_pipeline_cascade_exhausted_returns_honest_empty():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _ExhaustingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    result = pipeline.run_turn(_situation("something unknowable"))

    assert result.cascade_result.status == CascadeStatus.EXHAUSTED
    assert "didn't find anything" in result.reply_text.lower()
    assert result.workflow_run is None
    assert result.decision_blob is None


# ── Path trace shape ────────────────────────────────────────────────────────


def test_pipeline_trace_summary_formats():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _MatchingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)
    result = pipeline.run_turn(_situation())

    summary = result.trace_summary()
    assert isinstance(summary, list)
    assert all(":" in line and "—" in line for line in summary)


# ── Blob constructors ───────────────────────────────────────────────────────


def test_blob_from_cascade_match_fields():
    situation = _situation("find things")
    mem = MagicMock()
    mem.narrative = "Igor knows about goal trees and memory architecture"
    cascade_result = CascadeResult(
        status=CascadeStatus.MATCHED,
        level_name="level_0_exact_recall",
        data=[mem],
        reason="hit",
    )
    blob = _blob_from_cascade_match(situation, cascade_result)
    assert blob.intent == Intent.ANSWER
    assert blob.selected_action
    assert "goal trees" in blob.selected_action
    assert "level_0_exact_recall" not in blob.selected_action
    assert blob.confidence == 0.8
    assert blob.provenance.maker == "substrate"


def test_blob_from_cascade_match_no_data_falls_back():
    situation = _situation("find things")
    cascade_result = CascadeResult(
        status=CascadeStatus.MATCHED,
        level_name="level_0_exact_recall",
        data=[],
        reason="hit",
    )
    blob = _blob_from_cascade_match(situation, cascade_result)
    assert blob.selected_action
    assert "substrate matched" in blob.selected_action


def test_blob_from_workflow_output_carries_proposed_experiment():
    from unseen_university.devices.igor.cognition.reasoning_workflow import (
        Conversation,
        WorkflowA_ExperimentDesign,
        WorkflowComplete,
        WorkflowRun,
    )

    wf_name = "workflow_a_experiment_design"
    proposed = ProposedExperiment(
        hypothesis="X leads to Y",
        probe="run check X",
        expected_observation="see Y",
    )
    run = WorkflowRun(
        workflow_name=wf_name,
        conversation=Conversation(workflow_name=wf_name),
        complete=WorkflowComplete(output=proposed, reason="done"),
        transition_count=1,
    )
    blob = _blob_from_workflow_output(_situation(), run)
    assert blob.intent == Intent.EXPERIMENT
    assert blob.proposed_experiment is proposed
    assert blob.hypothesis == "X leads to Y"
    # CP6: no selected_action when there's a proposed experiment to run
    assert blob.selected_action is None


def test_blob_from_workflow_non_experiment_output_defers():
    from unseen_university.devices.igor.cognition.reasoning_workflow import (
        Conversation,
        WorkflowComplete,
        WorkflowRun,
    )

    run = WorkflowRun(
        workflow_name="wf",
        conversation=Conversation(workflow_name="wf"),
        complete=WorkflowComplete(output="some string", reason="unusual"),
        transition_count=1,
    )
    blob = _blob_from_workflow_output(_situation(), run)
    assert blob.intent == Intent.DEFER
    assert blob.proposed_experiment is None


# ── Voice production failure path ───────────────────────────────────────────


def test_pipeline_voice_failure_captured_in_trace():
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _MatchingLevel())

    class _BrokenVoice(VoiceProducer):
        def produce(self, blob, ctx):
            raise RuntimeError("voice pipeline down")

    pipeline = TurnPipeline(cortex, cascade=cascade, voice_producer=_BrokenVoice())
    result = pipeline.run_turn(_situation())

    assert "voice production failed" in result.reply_text.lower()
    failed_steps = [e for e in result.path_trace if e.step == PathStep.VOICE_PRODUCTION]
    assert failed_steps
    assert failed_steps[0].status == "failed"


# ── Default construction works without injection ───────────────────────────


def test_pipeline_default_construction_does_not_crash():
    """Passing just cortex should build a working pipeline."""
    cortex = _mock_cortex()
    pipeline = TurnPipeline(cortex)
    # Don't run a turn — the default cascade hits cortex.search which is
    # mocked to return [], but the pipeline should at least instantiate.
    assert pipeline.cascade is not None
    assert pipeline.workflow is not None
    assert pipeline.voice_producer is not None
    assert pipeline.scheduler is not None


# ── Experiment enqueue from workflow ──────────────────────────────────────


def test_pipeline_escalation_enqueues_experiment():
    """When workflow produces a ProposedExperiment, the pipeline should
    convert it to a full Experiment and enqueue via the scheduler."""
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _EscalatingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    peer = _ScriptedPeer(
        ["Probe: run cortex.search('igor dev'). Expected: at least one facia result."]
    )
    result = pipeline.run_turn(_situation("find igor dev"), peer_advisor=peer)

    assert result.enqueued_experiment_ids
    assert len(result.enqueued_experiment_ids) == 1
    enqueue_traces = [
        e for e in result.path_trace if e.step == PathStep.EXPERIMENT_ENQUEUE
    ]
    assert enqueue_traces
    assert enqueue_traces[0].status == "enqueued"
    assert enqueue_traces[0].metadata.get("experiment_id")
    assert enqueue_traces[0].metadata.get("blob_id")


def test_pipeline_cascade_match_no_experiment_enqueued():
    """Cascade match path has no proposed_experiment — nothing to enqueue."""
    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _MatchingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    result = pipeline.run_turn(_situation("find the goal tree"))

    assert result.enqueued_experiment_ids == []
    enqueue_traces = [
        e for e in result.path_trace if e.step == PathStep.EXPERIMENT_ENQUEUE
    ]
    assert not enqueue_traces


def test_pipeline_experiment_enqueue_failure_captured_in_trace():
    """If scheduler.enqueue fails, the trace records the failure and
    the pipeline still produces a reply."""
    from unittest.mock import patch

    cortex = _mock_cortex()
    cascade = _make_cascade(cortex, _EscalatingLevel())
    pipeline = TurnPipeline(cortex, cascade=cascade)

    peer = _ScriptedPeer(["Probe: run cortex.search('test'). Expected: results."])

    with patch.object(
        pipeline.scheduler, "enqueue", side_effect=RuntimeError("db down")
    ):
        result = pipeline.run_turn(_situation("test query"), peer_advisor=peer)

    assert result.enqueued_experiment_ids == []
    enqueue_traces = [
        e for e in result.path_trace if e.step == PathStep.EXPERIMENT_ENQUEUE
    ]
    assert enqueue_traces
    assert enqueue_traces[0].status == "failed"
    assert result.reply_text
