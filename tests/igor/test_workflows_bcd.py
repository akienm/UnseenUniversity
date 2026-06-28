"""
test_workflows_bcd.py — T-workflow-b-evaluate-claim, T-workflow-c-diagnose-pattern, T-workflow-d-plan

Tests for Workflows B, C, D using scripted peers. No real LLM.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.reasoning_workflow import (  # noqa: E402
    ActionPlan,
    ClaimEvaluation,
    Conversation,
    PatternDiagnosis,
    PeerAdvisor,
    Speaker,
    WorkflowB_EvaluateClaim,
    WorkflowC_DiagnosePattern,
    WorkflowComplete,
    WorkflowD_Plan,
    WorkflowRecorder,
    WorkflowUtterance,
    _extract_confidence,
    _extract_numbered_items,
    run_workflow,
)


class ScriptedPeer(PeerAdvisor):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def respond(self, conversation: Conversation) -> str:
        if not self._responses:
            return "(out of responses)"
        return self._responses.pop(0)


# ── WorkflowB: Evaluate Claim ─────────────────────────────────────────────


class TestWorkflowB:
    def test_opening_contains_claim(self):
        wf = WorkflowB_EvaluateClaim()
        u = wf.opening_utterance({"claim": "memory X is still valid"})
        assert "memory X is still valid" in u.content
        assert u.speaker == Speaker.IGOR
        assert u.metadata["claim"] == "memory X is still valid"

    def test_completes_on_verdict_and_confidence(self):
        wf = WorkflowB_EvaluateClaim()
        peer = ScriptedPeer(
            ["Verdict: holds. Confidence: 0.85. The evidence is consistent."]
        )
        run = run_workflow(wf, {"claim": "test claim"}, peer)
        assert not run.complete.aborted
        output = run.complete.output
        assert isinstance(output, ClaimEvaluation)
        assert output.verdict is not None
        assert output.confidence > 0

    def test_asks_for_missing_verdict(self):
        wf = WorkflowB_EvaluateClaim()
        peer = ScriptedPeer(
            [
                "I'm somewhat confident about this.",
                "Verdict: weakened. Confidence: 0.4. Counter-evidence: recent data contradicts.",
            ]
        )
        run = run_workflow(wf, {"claim": "stale claim"}, peer)
        assert not run.complete.aborted
        assert isinstance(run.complete.output, ClaimEvaluation)
        assert run.complete.output.counter_evidence

    def test_asks_for_missing_confidence(self):
        wf = WorkflowB_EvaluateClaim()
        peer = ScriptedPeer(
            [
                "Verdict: refuted based on new data.",
                "Verdict: refuted. Confidence: 0.9.",
            ]
        )
        run = run_workflow(wf, {"claim": "wrong claim"}, peer)
        assert not run.complete.aborted

    def test_aborts_on_max_turns(self):
        wf = WorkflowB_EvaluateClaim()
        peer = ScriptedPeer(["I'm not sure." for _ in range(20)])
        run = run_workflow(wf, {"claim": "test"}, peer, max_turns=4)
        assert run.complete.aborted

    def test_output_struct_from_conversation(self):
        wf = WorkflowB_EvaluateClaim()
        conv = Conversation(workflow_name=wf.name)
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.IGOR,
                content="test",
                metadata={"opening": True, "claim": "my claim"},
            )
        )
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.PEER, content="Verdict: holds. Confidence: 0.8."
            )
        )
        result = wf.output_struct(conv)
        assert isinstance(result, ClaimEvaluation)
        assert result.claim == "my claim"

    def test_name(self):
        assert WorkflowB_EvaluateClaim.name == "workflow_b_evaluate_claim"


# ── WorkflowC: Diagnose Pattern ───────────────────────────────────────────


class TestWorkflowC:
    def test_opening_contains_pattern(self):
        wf = WorkflowC_DiagnosePattern()
        u = wf.opening_utterance({"pattern": "Igor stalls after tool calls"})
        assert "Igor stalls after tool calls" in u.content
        assert u.metadata["pattern"] == "Igor stalls after tool calls"

    def test_completes_on_hypothesis_and_test(self):
        wf = WorkflowC_DiagnosePattern()
        peer = ScriptedPeer(
            [
                "Hypothesis: the pe_chain hits scope guards silently. "
                "Test: check if MEDIUM inertia files are being written without "
                "logging the block reason."
            ]
        )
        run = run_workflow(wf, {"pattern": "silent stalls"}, peer)
        assert not run.complete.aborted
        output = run.complete.output
        assert isinstance(output, PatternDiagnosis)
        assert output.hypothesis
        assert output.proposed_test

    def test_asks_for_missing_hypothesis(self):
        wf = WorkflowC_DiagnosePattern()
        peer = ScriptedPeer(
            [
                "Test: try disabling the scope guard.",
                "Hypothesis: the scope guard is too aggressive. "
                "Test: try disabling the scope guard temporarily.",
            ]
        )
        run = run_workflow(wf, {"pattern": "repeated failures"}, peer)
        assert not run.complete.aborted
        assert isinstance(run.complete.output, PatternDiagnosis)

    def test_asks_for_missing_test(self):
        wf = WorkflowC_DiagnosePattern()
        peer = ScriptedPeer(
            [
                "This is likely because the habit network has gaps.",
                "Hypothesis: habit gaps. Test: check activation counts "
                "for the expected habit.",
            ]
        )
        run = run_workflow(wf, {"pattern": "no reply"}, peer)
        assert not run.complete.aborted

    def test_aborts_on_max_turns(self):
        wf = WorkflowC_DiagnosePattern()
        peer = ScriptedPeer(["Hmm interesting." for _ in range(20)])
        run = run_workflow(wf, {"pattern": "test"}, peer, max_turns=4)
        assert run.complete.aborted

    def test_output_struct_from_conversation(self):
        wf = WorkflowC_DiagnosePattern()
        conv = Conversation(workflow_name=wf.name)
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.IGOR,
                content="test",
                metadata={"opening": True, "pattern": "stalls"},
            )
        )
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.PEER,
                content="Hypothesis: missing habit. Test: check activations.",
            )
        )
        result = wf.output_struct(conv)
        assert isinstance(result, PatternDiagnosis)
        assert result.pattern == "stalls"

    def test_name(self):
        assert WorkflowC_DiagnosePattern.name == "workflow_c_diagnose_pattern"


# ── WorkflowD: Plan ───────────────────────────────────────────────────────


class TestWorkflowD:
    def test_opening_contains_goal(self):
        wf = WorkflowD_Plan()
        u = wf.opening_utterance({"goal": "migrate to new DB schema"})
        assert "migrate to new DB schema" in u.content
        assert u.metadata["goal"] == "migrate to new DB schema"

    def test_completes_on_numbered_steps(self):
        wf = WorkflowD_Plan()
        peer = ScriptedPeer(
            [
                "Here's the plan:\n"
                "1. Back up the current database\n"
                "2. Run the migration script\n"
                "3. Verify data integrity\n\n"
                "Risks:\n- Data loss if backup fails\n\n"
                "Start with: back up the database first."
            ]
        )
        run = run_workflow(wf, {"goal": "migrate DB"}, peer)
        assert not run.complete.aborted
        output = run.complete.output
        assert isinstance(output, ActionPlan)
        assert len(output.steps) >= 2
        assert output.first_step

    def test_asks_for_steps_when_missing(self):
        wf = WorkflowD_Plan()
        peer = ScriptedPeer(
            [
                "That's a complex task, let me think about it.",
                "1. First, analyze the schema diff\n"
                "2. Write migration SQL\n"
                "3. Test on staging\n"
                "Start by analyzing the schema diff.",
            ]
        )
        run = run_workflow(wf, {"goal": "migrate DB"}, peer)
        assert not run.complete.aborted
        assert isinstance(run.complete.output, ActionPlan)
        assert len(run.complete.output.steps) >= 2

    def test_aborts_on_max_turns(self):
        wf = WorkflowD_Plan()
        peer = ScriptedPeer(["Let me think more." for _ in range(20)])
        run = run_workflow(wf, {"goal": "test"}, peer, max_turns=4)
        assert run.complete.aborted

    def test_output_struct_from_conversation(self):
        wf = WorkflowD_Plan()
        conv = Conversation(workflow_name=wf.name)
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.IGOR,
                content="test",
                metadata={"opening": True, "goal": "fix the thing"},
            )
        )
        conv.add(
            WorkflowUtterance(
                speaker=Speaker.PEER,
                content="1. Read the logs\n2. Find the error\n3. Fix it\nStart by reading the logs.",
            )
        )
        result = wf.output_struct(conv)
        assert isinstance(result, ActionPlan)
        assert result.goal == "fix the thing"
        assert len(result.steps) >= 2

    def test_uses_first_step_as_default(self):
        wf = WorkflowD_Plan()
        peer = ScriptedPeer(["1. Check the config\n2. Update the setting\n3. Restart"])
        run = run_workflow(wf, {"goal": "fix config"}, peer)
        assert not run.complete.aborted
        assert run.complete.output.first_step

    def test_name(self):
        assert WorkflowD_Plan.name == "workflow_d_plan"

    def test_includes_constraints(self):
        wf = WorkflowD_Plan()
        u = wf.opening_utterance({"goal": "deploy", "constraints": "no downtime"})
        assert "no downtime" in u.content

    def test_includes_resources(self):
        wf = WorkflowD_Plan()
        u = wf.opening_utterance({"goal": "deploy", "resources": "2 servers available"})
        assert "2 servers available" in u.content


# ── Extraction helpers ─────────────────────────────────────────────────────


class TestExtractionHelpers:
    def test_extract_confidence_percentage(self):
        assert _extract_confidence("confidence: 85%") == 0.85

    def test_extract_confidence_decimal(self):
        assert _extract_confidence("confidence: 0.7") == 0.7

    def test_extract_confidence_verbal_high(self):
        assert _extract_confidence("I'm very confident") == 0.9

    def test_extract_confidence_verbal_moderate(self):
        assert _extract_confidence("I'm somewhat sure") == 0.6

    def test_extract_confidence_verbal_low(self):
        assert _extract_confidence("low confidence here") == 0.3

    def test_extract_confidence_default(self):
        assert _extract_confidence("no clue") == 0.5

    def test_extract_numbered_items(self):
        text = "Here:\n1. First thing\n2. Second thing\n3. Third thing"
        items = _extract_numbered_items(text)
        assert len(items) >= 2
        assert "First thing" in items[0]

    def test_extract_numbered_items_parens(self):
        text = "1) Do A\n2) Do B\n3) Do C"
        items = _extract_numbered_items(text)
        assert len(items) >= 2

    def test_extract_numbered_items_empty(self):
        assert _extract_numbered_items("no numbers here") == []


# ── Recorder integration ───────────────────────────────────────────────────


def test_recorder_tracks_all_three_workflows():
    recorder = WorkflowRecorder()
    for WF, situation, response in [
        (WorkflowB_EvaluateClaim, {"claim": "X"}, "Verdict: holds. Confidence: 0.8."),
        (WorkflowC_DiagnosePattern, {"pattern": "Y"}, "Hypothesis: Z. Test: check it."),
        (WorkflowD_Plan, {"goal": "G"}, "1. Do A\n2. Do B\nStart by doing A."),
    ]:
        wf = WF()
        peer = ScriptedPeer([response])
        run = run_workflow(wf, situation, peer, recorder=recorder)
        assert not run.complete.aborted
    assert len(recorder.records()) >= 3
