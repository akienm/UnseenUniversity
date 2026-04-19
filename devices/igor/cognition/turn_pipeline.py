"""
turn_pipeline.py — T-turn-pipeline-module

The conductor that strings cascade + workflow + prompt contexts + voice
into one orchestrator. Single entry point `TurnPipeline.run_turn(situation)`
produces a `TurnResult` carrying the reply text plus full path trace.

## Architectural role (Akien 2026-04-19)

This module is the NEW inference path that implements the biomimetic
model: trees reason, escalate to upstream via conversational back-and-
forth when reasoning fails, return to trees for output. Trees are one
voice actor; LLM is a separate voice actor. When LLM is involved, it's
invoked at least twice per turn (once for reasoning, once for voice)
with small focused payloads — not one fat system-prompt one-shot.

The LEGACY path (direct reasoner.reason() calls in main.py with
build_system_prompt() as a big preamble) is being retired — see
T-retire-legacy-direct-reasoner-path. All interactive turns are being
migrated through this pipeline.

## Pipeline (per T-reasoning-voice-split #436 design session 2026-04-15)

## Pipeline (per T-reasoning-voice-split #436 design session 2026-04-15)

    situation
       │
       ▼
  [1] CASCADE WALK (ExperimentCascade.attempt)
       │
       ├── MATCHED at level 0-4  → skip to [4] with cascade.data as
       │                            the selected_action candidate
       │
       └── ESCALATE at level 5   → [2] run workflow
       │
       ▼
  [2] REASONING WORKFLOW (run_workflow(WorkflowA, situation, peer))
       │
       │  Output: ProposedExperiment (or equivalent typed struct)
       │
       ▼
  [3] DECISION BLOB — build from workflow output, run can_commit() gate
       │  (CP6: if can_commit=False, the blob's proposed_experiment
       │   would be enqueued in a real wiring; this MVP just records)
       │
       ▼
  [4] VOICE CONTEXT (prompt_contexts.voice_context)
       │
       ▼
  [5] VOICE PRODUCTION (stub — renders blob.selected_action as text;
       actual LLM voice call lives in T-llm-collaboration-protocol)
       │
       ▼
  TurnResult(reply_text, path_trace, ...)

## CP grounding

- CP1 — path_trace records what was known vs. inferred at every step
- CP3 — every step records its own provenance in path_trace
- CP6 — reasoning outputs NEVER flow directly to voice without first
  passing through decision_blob.can_commit(); if can_commit blocks,
  the path_trace captures the gating reason
"""

from __future__ import annotations

from ..igor_base import IgorBase

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from .decision_blob import (
    DecisionBlob,
    Intent,
    ProposedExperiment,
    Provenance as DBProvenance,
)
from .experiment_cascade import (
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    ExperimentCascade,
    build_default_cascade,
)
from .prompt_contexts import (
    PromptContext,
    Provenance as PCProvenance,
    voice_context,
)
from .experiment import Experiment, from_proposed
from .experiment_scheduler import ExperimentScheduler
from .reasoning_workflow import (
    PeerAdvisor,
    Workflow,
    WorkflowA_ExperimentDesign,
    WorkflowRecorder,
    WorkflowRun,
    run_workflow,
)

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


# ── Path trace ──────────────────────────────────────────────────────────────


class PathStep(str, Enum):
    """Labels for the stages a turn walks through."""

    CASCADE = "cascade"
    WORKFLOW = "workflow"
    DECISION_BLOB = "decision_blob"
    CAN_COMMIT = "can_commit"
    EXPERIMENT_ENQUEUE = "experiment_enqueue"
    VOICE_CONTEXT = "voice_context"
    VOICE_PRODUCTION = "voice_production"


@dataclass
class TraceEntry:
    """One stage's outcome in the pipeline."""

    step: PathStep
    status: str
    """Short status string: 'ok', 'matched', 'escalated', 'blocked', etc."""

    summary: str = ""
    """Human-readable one-liner."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """End-to-end output of a single pipeline run."""

    reply_text: str
    cascade_result: Optional[CascadeResult] = None
    workflow_run: Optional[WorkflowRun] = None
    decision_blob: Optional[DecisionBlob] = None
    voice_context: Optional[PromptContext] = None
    enqueued_experiment_ids: list[str] = field(default_factory=list)
    path_trace: list[TraceEntry] = field(default_factory=list)

    def trace_summary(self) -> list[str]:
        return [f"{e.step.value}:{e.status} — {e.summary}" for e in self.path_trace]


# ── Voice production (stub) ─────────────────────────────────────────────────


class VoiceProducer:
    """Stub voice producer — renders a decision blob as plain text.

    The real wiring (T-llm-collaboration-protocol) replaces this with
    an LLM call using voice_context(). This stub keeps the pipeline
    testable and lets us ship the conductor before voice actors land.
    """

    def produce(self, blob: DecisionBlob, ctx: PromptContext) -> str:
        """Render a DecisionBlob as text.

        Igor voice has TWO channels of uncertainty-texture, kept
        separate (see feedback_igor_voice_register.md):

          1. Epistemic / self-knowledge channel = CERTAIN. Igor is
             certain of his current best guess about the world and
             certain of who he is. Both are earned through
             experimentation, not inherited. No epistemic hedges.

          2. Social / master-facing channel = APPROPRIATELY UNCERTAIN.
             The Pratchett Igor servile-tinker flavor ('will master be
             pleased?') is correct here — interpersonal register, not
             knowledge register. Subtle lisp allowed, never overdone.

        This stub only handles channel 1 (commits cleanly, no apology).
        Channel 2 flavoring lands with T-llm-collaboration-protocol +
        T-igor-writes-character-sheet.
        """
        action = getattr(blob, "selected_action", None)
        hypothesis = getattr(blob, "hypothesis", "") or ""
        proposed = getattr(blob, "proposed_experiment", None)

        # Case 1: committed selected_action — render it directly
        if action:
            return action

        # Case 2: hypothesis + proposed experiment — current best guess
        # plus how we're going to test it. Confident in process; the
        # answer being provisional is stated as fact, not apology.
        if proposed is not None:
            probe = getattr(proposed, "probe", "") or ""
            expected = getattr(proposed, "expected_observation", "") or ""
            parts = []
            if hypothesis:
                parts.append(f"Current best guess: {hypothesis[:300]}")
            parts.append(f"Testing it: {probe[:300]}")
            if expected:
                parts.append(f"What I expect to see: {expected[:300]}")
            return "\n\n".join(parts)

        # Case 3: fallthrough — best guess if we have one, else direct statement
        if hypothesis:
            return f"Current best guess: {hypothesis[:300]}"
        return "Still working on this one."


class ABVoiceProducer(VoiceProducer, IgorBase):
    """Voice producer backed by the A/B framework (T-output-shaping-trees-wire-in).

    Runs both GraphVoiceActor (generation word graph) and LLMVoiceActor
    in parallel, compares, picks winner, trains graph from LLM when LLM
    wins. Falls back to the stub VoiceProducer if the framework isn't
    available or fails.
    """

    def __init__(
        self,
        cortex: "Cortex",
        gateway: Optional[Any] = None,
        word_graph: Optional[Any] = None,
    ) -> None:
        self._cortex = cortex
        self._gateway = gateway
        self._word_graph = word_graph
        self._framework = None
        self._stub = VoiceProducer()

    def _ensure_framework(self) -> bool:
        if self._framework is not None:
            return True
        try:
            from .voice_ab import GraphVoiceActor, LLMVoiceActor, VoiceABFramework

            graph_actor = GraphVoiceActor(word_graph=self._word_graph)
            llm_actor = LLMVoiceActor(cortex=self._cortex, gateway=self._gateway)
            self._framework = VoiceABFramework(
                graph_actor=graph_actor,
                llm_actor=llm_actor,
                word_graph=self._word_graph,
            )
            return True
        except Exception as exc:
            logger.debug("ABVoiceProducer framework init failed: %s", exc)
            return False

    def produce(self, blob: DecisionBlob, ctx: PromptContext) -> str:
        if not self._ensure_framework():
            return self._stub.produce(blob, ctx)
        try:
            result = self._framework.produce(blob, ctx)
            if result:
                return result
        except Exception as exc:
            logger.warning("ABVoiceProducer.produce failed: %s", exc)
        return self._stub.produce(blob, ctx)

    def stats(self) -> dict:
        if self._framework:
            return self._framework.stats()
        return {"total": 0, "graph_wins": 0, "llm_wins": 0, "graduation_pct": 0.0}


# ── The conductor ───────────────────────────────────────────────────────────


class TurnPipeline(IgorBase):
    """Orchestrates cascade + workflow + decision_blob + voice for a
    single turn. Construct once per process; call `run_turn(situation)`
    per input.

    All dependencies can be injected for testing: cascade, workflow,
    voice_producer, peer_advisor. Defaults build a live ExperimentCascade
    and WorkflowA_ExperimentDesign.
    """

    def __init__(
        self,
        cortex: "Cortex",
        *,
        cascade: Optional[ExperimentCascade] = None,
        workflow: Optional[Workflow] = None,
        voice_producer: Optional[VoiceProducer] = None,
        recorder: Optional[WorkflowRecorder] = None,
        scheduler: Optional[ExperimentScheduler] = None,
    ) -> None:
        self.cortex = cortex
        self.cascade = cascade or build_default_cascade(cortex)
        self.workflow = workflow or WorkflowA_ExperimentDesign()
        self.voice_producer = voice_producer or VoiceProducer()
        self.recorder = recorder or WorkflowRecorder()
        self.scheduler = scheduler or ExperimentScheduler(cortex)

    def run_turn(
        self,
        situation: CascadeSituation,
        *,
        peer_advisor: Optional[PeerAdvisor] = None,
    ) -> TurnResult:
        """Drive a single turn through the pipeline. peer_advisor is
        required when reasoning escalation actually runs a workflow;
        if omitted and cascade ESCALATES, the turn returns a hedged
        reply explaining the missing peer.
        """
        trace: list[TraceEntry] = []

        # [1] CASCADE WALK
        cascade_result = self.cascade.attempt(situation)
        trace.append(
            TraceEntry(
                step=PathStep.CASCADE,
                status=cascade_result.status.value,
                summary=(f"{cascade_result.level_name}: {cascade_result.reason[:120]}"),
                metadata={
                    "level_name": cascade_result.level_name,
                    "status": cascade_result.status.value,
                },
            )
        )

        # Cascade matched below level 5 → we already have an answer.
        if cascade_result.status == CascadeStatus.MATCHED:
            return self._assemble_match_result(situation, cascade_result, trace)

        # Cascade exhausted without levers and without escalating → no reply
        if cascade_result.status == CascadeStatus.EXHAUSTED:
            return self._assemble_exhausted_result(situation, cascade_result, trace)

        # Cascade escalated (status == ESCALATE) → run reasoning workflow
        return self._escalate_to_workflow(
            situation, cascade_result, trace, peer_advisor
        )

    # ── Assembly paths ──────────────────────────────────────────────────────

    def _assemble_match_result(
        self,
        situation: CascadeSituation,
        cascade_result: CascadeResult,
        trace: list[TraceEntry],
    ) -> TurnResult:
        """Cascade matched at a substrate level. Build a minimal
        DecisionBlob around the match and hand to voice production."""
        blob = _blob_from_cascade_match(situation, cascade_result)
        trace.append(
            TraceEntry(
                step=PathStep.DECISION_BLOB,
                status="built_from_cascade_match",
                summary=f"intent={blob.intent.value}",
            )
        )

        safe, reasons = blob.can_commit()
        trace.append(
            TraceEntry(
                step=PathStep.CAN_COMMIT,
                status="safe" if safe else "blocked",
                summary=("; ".join(reasons) if reasons else "CP-gate passed"),
            )
        )

        return self._produce_voice(situation, blob, cascade_result, None, trace)

    def _escalate_to_workflow(
        self,
        situation: CascadeSituation,
        cascade_result: CascadeResult,
        trace: list[TraceEntry],
        peer_advisor: Optional[PeerAdvisor],
    ) -> TurnResult:
        """Cascade escalated. Run reasoning workflow, build blob from
        the workflow output, check can_commit, then voice."""
        if peer_advisor is None:
            # No peer available → return a hedged reply explaining the gap
            trace.append(
                TraceEntry(
                    step=PathStep.WORKFLOW,
                    status="skipped_no_peer",
                    summary="cascade escalated but no peer_advisor provided",
                )
            )
            return TurnResult(
                reply_text=(
                    "I'm stuck on this one — my substrate didn't resolve it "
                    "and I don't have a reasoning peer available to consult "
                    "right now. I'd need to either wait for more data or "
                    "have the peer wired up to make progress."
                ),
                cascade_result=cascade_result,
                path_trace=trace,
            )

        workflow_situation = {
            "uncertainty": situation.query,
            "current_state": (f"cascade exhausted at {cascade_result.level_name}"),
            "what_i_tried": cascade_result.reason,
        }

        workflow_run = run_workflow(
            self.workflow,
            workflow_situation,
            peer_advisor,
            recorder=self.recorder,
        )
        trace.append(
            TraceEntry(
                step=PathStep.WORKFLOW,
                status="aborted" if workflow_run.complete.aborted else "completed",
                summary=workflow_run.complete.reason,
                metadata={
                    "transition_count": workflow_run.transition_count,
                    "workflow_name": workflow_run.workflow_name,
                },
            )
        )

        if workflow_run.complete.aborted or not workflow_run.complete.output:
            return TurnResult(
                reply_text=(
                    "I consulted with my reasoning peer but we didn't reach a "
                    "concrete next step. I'd rather say that honestly than "
                    "fake an answer."
                ),
                cascade_result=cascade_result,
                workflow_run=workflow_run,
                path_trace=trace,
            )

        blob = _blob_from_workflow_output(situation, workflow_run)
        trace.append(
            TraceEntry(
                step=PathStep.DECISION_BLOB,
                status="built_from_workflow",
                summary=f"intent={blob.intent.value}",
            )
        )

        safe, reasons = blob.can_commit()
        trace.append(
            TraceEntry(
                step=PathStep.CAN_COMMIT,
                status="safe" if safe else "blocked",
                summary="; ".join(reasons) if reasons else "CP-gate passed",
            )
        )

        return self._produce_voice(situation, blob, cascade_result, workflow_run, trace)

    def _assemble_exhausted_result(
        self,
        situation: CascadeSituation,
        cascade_result: CascadeResult,
        trace: list[TraceEntry],
    ) -> TurnResult:
        """Cascade exhausted without ESCALATE — nothing more to try."""
        return TurnResult(
            reply_text=(
                "I looked through everything I know and didn't find anything "
                f"directly relevant to {situation.query!r}. I'll sit with "
                "this and see if more context surfaces."
            ),
            cascade_result=cascade_result,
            path_trace=trace,
        )

    def _enqueue_experiments(
        self,
        blob: DecisionBlob,
        trace: list[TraceEntry],
    ) -> list[str]:
        """Convert blob's proposed_experiment to a full Experiment and enqueue."""
        if blob.proposed_experiment is None:
            return []
        try:
            experiment = from_proposed(
                blob.proposed_experiment,
                source=blob.provenance.maker if blob.provenance else "unknown",
                confidence=blob.confidence,
                parent_blob_id=blob.blob_id,
            )
            exp_id = self.scheduler.enqueue(experiment)
            trace.append(
                TraceEntry(
                    step=PathStep.EXPERIMENT_ENQUEUE,
                    status="enqueued",
                    summary=f"{exp_id} — {experiment.hypothesis.statement[:80]}",
                    metadata={"experiment_id": exp_id, "blob_id": blob.blob_id},
                )
            )
            return [exp_id]
        except Exception as exc:
            logger.warning("experiment enqueue failed: %s", exc)
            trace.append(
                TraceEntry(
                    step=PathStep.EXPERIMENT_ENQUEUE,
                    status="failed",
                    summary=f"{type(exc).__name__}: {exc}",
                )
            )
            return []

    def _produce_voice(
        self,
        situation: CascadeSituation,
        blob: DecisionBlob,
        cascade_result: CascadeResult,
        workflow_run: Optional[WorkflowRun],
        trace: list[TraceEntry],
    ) -> TurnResult:
        """Build voice context + run voice producer."""
        enqueued = self._enqueue_experiments(blob, trace)

        pc_prov = PCProvenance(
            caller="turn_pipeline",
            situation_source=f"cascade:{cascade_result.level_name}",
        )
        ctx = voice_context(blob, provenance=pc_prov)
        trace.append(
            TraceEntry(
                step=PathStep.VOICE_CONTEXT,
                status="built",
                summary=f"phase={ctx.phase}, sections={len(ctx.sections)}",
            )
        )

        try:
            reply_text = self.voice_producer.produce(blob, ctx)
        except Exception as exc:
            logger.warning("voice production failed: %s", exc)
            reply_text = (
                "(voice production failed — the decision was made but I "
                "couldn't render it cleanly as a reply)"
            )
            trace.append(
                TraceEntry(
                    step=PathStep.VOICE_PRODUCTION,
                    status="failed",
                    summary=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            trace.append(
                TraceEntry(
                    step=PathStep.VOICE_PRODUCTION,
                    status="ok",
                    summary=f"{len(reply_text)} chars",
                )
            )

        return TurnResult(
            reply_text=reply_text,
            cascade_result=cascade_result,
            workflow_run=workflow_run,
            decision_blob=blob,
            voice_context=ctx,
            enqueued_experiment_ids=enqueued,
            path_trace=trace,
        )


# ── Blob constructors ──────────────────────────────────────────────────────


def _extract_cascade_content(cascade_result: CascadeResult) -> str:
    """Pull the best narrative text from cascade match data."""
    data = cascade_result.data
    if not data:
        return ""
    if isinstance(data, list):
        for item in data:
            narrative = getattr(item, "narrative", None)
            if narrative:
                return str(narrative)[:500]
        return str(data[0])[:500] if data else ""
    if isinstance(data, dict):
        return str(data.get("narrative", data.get("content", "")))[:500]
    return str(data)[:500]


def _blob_from_cascade_match(
    situation: CascadeSituation,
    cascade_result: CascadeResult,
) -> DecisionBlob:
    """Build a DecisionBlob for a cascade that matched at a substrate
    level. The selected_action carries the matched memory content;
    confidence is relatively high because substrate resolved it without
    needing to escalate to reasoning.
    """
    content = _extract_cascade_content(cascade_result)
    prov = DBProvenance(
        maker="substrate",
        inputs=[cascade_result.level_name, situation.query[:80]],
    )
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action=content
        or f"(substrate matched at {cascade_result.level_name} but no content extracted)",
        confidence=0.8,
        provenance=prov,
    )
    return blob


def _blob_from_workflow_output(
    situation: CascadeSituation,
    workflow_run: WorkflowRun,
) -> DecisionBlob:
    """Build a DecisionBlob from a workflow completion. The workflow
    output becomes a ProposedExperiment on the blob — which means
    can_commit will block without a prior experiment run (CP6). This
    is correct: peer-proposed experiments should be enqueued and run,
    not committed to.
    """
    output = workflow_run.complete.output
    prov = DBProvenance(
        maker="reasoning_llm",
        inputs=[f"workflow:{workflow_run.workflow_name}", situation.query[:80]],
    )
    proposed = output if isinstance(output, ProposedExperiment) else None
    blob = DecisionBlob(
        intent=Intent.EXPERIMENT if proposed else Intent.DEFER,
        hypothesis=(
            proposed.hypothesis
            if proposed
            else "workflow produced non-experiment output"
        ),
        confidence=0.5,
        provenance=prov,
        proposed_experiment=proposed,
    )
    return blob
