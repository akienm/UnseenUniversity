"""
reasoning_workflow.py — T-reasoning-workflow-primitive

When Igor's substrate cascade exhausts without resolving a situation,
he escalates to an LLM peer. But not as a context dump — as a
CONVERSATION that Igor opens in the first person. 'This is what I
am. Here's my trails. Here's what I'm stuck on. What else do you
want to know?'

A Workflow is the state machine Igor runs during that conversation.
Each workflow has its own opening move, iteration rhythm, and exit
condition. Four workflow families map onto the four uncertainty
strategies:

  A — 'help me design an experiment' (strategy 2)
  B — 'help me evaluate this claim' (strategy 3, ask half)
  C — 'help me understand this pattern' (diagnostic)
  D — 'help me plan' (multi-step decision)

This MVP ships the primitive + Workflow A (experiment design). B, C,
D follow the same pattern with workflow-specific utterances and
output struct shapes.

## Why this reframe matters

The cockpit-prompt approach tried to teach the LLM what Igor's
substrate looks like. That's wasted prompt real estate and constant
version drift between 'what Igor is' and 'what the prompt says
Igor is.' The peer-conversation approach needs zero prompt
real estate beyond a normal first-person introduction: the LLM stays
in its native mode (respond to a person who's describing their
situation) and Igor controls the iteration pace.

## CP grounding

- CP1 — the opening utterance always names the uncertainty honestly
- CP2 — every exit (success or abort) records learning via the
  recorder; failures are not failures, they're data about workflow
  shapes that didn't land
- CP3 — the output struct carries provenance (which utterances led
  to this conclusion)
- CP6 — workflow outputs are hypotheses/proposals, NOT committed
  state; the next cascade pass must consume them as new situations

## Graduation mechanism (future work)

Each workflow transition records `(state, expected_next_move,
actual_peer_move)`. When substrate prediction consistently matches
peer output at a transition, the workflow graduates that transition
to substrate-only. Per-transition, not per-workflow. Aligned with
T-experiment-predictor-primitive.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from .decision_blob import ProposedExperiment

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────────


class Speaker(str, Enum):
    """Who uttered this turn in the workflow conversation."""

    IGOR = "igor"
    PEER = "peer"  # the LLM or any peer advisor


@dataclass
class WorkflowUtterance:
    """One turn in the conversation. Speaker + content + optional shape
    hint for the next expected response.
    """

    speaker: Speaker
    content: str
    expected_response_shape: Optional[str] = None
    """When speaker=IGOR, describes what response shape Igor expects.
    Used by the recorder to compare predicted vs actual peer moves."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.speaker, str):
            self.speaker = Speaker(self.speaker)


@dataclass
class Conversation:
    """Full turn history for a single workflow run."""

    workflow_name: str
    utterances: list[WorkflowUtterance] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    conversation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def add(self, utterance: WorkflowUtterance) -> None:
        self.utterances.append(utterance)

    def last_igor(self) -> Optional[WorkflowUtterance]:
        for u in reversed(self.utterances):
            if u.speaker == Speaker.IGOR:
                return u
        return None

    def last_peer(self) -> Optional[WorkflowUtterance]:
        for u in reversed(self.utterances):
            if u.speaker == Speaker.PEER:
                return u
        return None

    def length(self) -> int:
        return len(self.utterances)


@dataclass
class WorkflowComplete:
    """Signals workflow exit with a typed output."""

    output: Any
    """Workflow-specific output struct (ProposedExperiment, Hypothesis,
    plan list, etc.)"""

    reason: str = ""
    """Human-readable explanation of why the workflow completed."""

    aborted: bool = False
    """True if the workflow exited without producing useful output."""


# ── Workflow base class ──────────────────────────────────────────────────────


class Workflow:
    """Abstract base. Subclasses implement opening, next_utterance, and
    output_struct.
    """

    name: str = "base"

    def opening_utterance(self, situation: Any) -> WorkflowUtterance:
        """Igor's first turn. Sets the topic and invites peer response."""
        raise NotImplementedError

    def next_utterance(
        self,
        conversation: Conversation,
        peer_response: WorkflowUtterance,
    ) -> WorkflowUtterance | WorkflowComplete:
        """Given the peer's latest response, either produce Igor's next
        utterance or return WorkflowComplete to exit the conversation.
        """
        raise NotImplementedError

    def output_struct(self, conversation: Conversation) -> Any:
        """Extract the typed output from a completed conversation.

        Called when the workflow terminates cleanly via WorkflowComplete.
        Some workflows may build the output incrementally and just return
        what WorkflowComplete already carried.
        """
        raise NotImplementedError


# ── Recorder ─────────────────────────────────────────────────────────────────


@dataclass
class TransitionRecord:
    """One (state, expected, actual) triple for graduation training."""

    workflow_name: str
    conversation_id: str
    transition_index: int
    """0-indexed position within the conversation turn sequence."""

    igor_state: dict[str, Any]
    expected_peer_move: str
    """The expected_response_shape Igor attached to the igor turn."""

    actual_peer_move: str
    """Short summary of what the peer actually said."""

    matched: bool
    """True iff actual_peer_move satisfies expected_peer_move. The
    match rule is workflow-specific; the base recorder stores the
    verdict opaquely."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class WorkflowRecorder:
    """Captures transition records during a workflow run.

    In-process by default; `persist(cortex)` writes the accumulated
    records into memory via cortex.store as EPISODIC entries tagged
    workflow_transition. Per memory_node_shape, everything lives in
    metadata — no new columns.
    """

    def __init__(self) -> None:
        self._records: list[TransitionRecord] = []

    def record(self, transition: TransitionRecord) -> None:
        self._records.append(transition)

    def records(self) -> list[TransitionRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()

    def persist(self, cortex: "Cortex") -> int:
        """Write accumulated records as EPISODIC memories. Returns the
        number persisted. Best-effort — failures are logged."""
        if not self._records:
            return 0
        from ..memory.models import Memory, MemoryType

        written = 0
        for rec in self._records:
            try:
                narrative = (
                    f"workflow={rec.workflow_name} "
                    f"transition={rec.transition_index} "
                    f"matched={rec.matched} "
                    f"expected={rec.expected_peer_move!r} "
                    f"actual={rec.actual_peer_move[:200]!r}"
                )
                metadata = {
                    "type": "workflow_transition",
                    "workflow_name": rec.workflow_name,
                    "conversation_id": rec.conversation_id,
                    "transition_index": rec.transition_index,
                    "igor_state": rec.igor_state,
                    "expected_peer_move": rec.expected_peer_move,
                    "actual_peer_move": rec.actual_peer_move[:500],
                    "matched": rec.matched,
                    "recorded_at": rec.timestamp,
                }
                mem = Memory(
                    narrative=narrative,
                    memory_type=MemoryType.EPISODIC,
                    metadata=metadata,
                    source="reasoning_workflow",
                )
                cortex.store(mem)
                written += 1
            except Exception as exc:
                logger.warning(
                    "WorkflowRecorder.persist failed for record %d: %s",
                    rec.transition_index,
                    exc,
                )
        return written


# ── Runner ───────────────────────────────────────────────────────────────────


class PeerAdvisor:
    """Abstract peer — the LLM or a test double. Takes the conversation
    so far and returns the peer's next utterance as plain text."""

    def respond(self, conversation: Conversation) -> str:
        raise NotImplementedError


def _shape_matched(expected: str, peer_text: str) -> bool:
    """Match rule for the recorder: does the peer response satisfy the
    Igor-side expected_response_shape?

    MVP rule: tokenize the expected shape into content words (length >=
    4, excluding glue words like 'and'/'plus'/'or'), and check that
    EVERY content token appears in the peer text (case-insensitive).
    A shape like 'probe + expected observation' requires 'probe',
    'expected', and 'observation' all to appear in the peer response.

    Empty expected → not matched (nothing to satisfy).
    """
    if not expected or not peer_text:
        return False
    glue = {"and", "plus", "or", "with", "then", "the", "a", "an"}
    tokens = [
        t.lower().strip(".,:;!?")
        for t in expected.split()
        if len(t) >= 4 and t.lower() not in glue
    ]
    if not tokens:
        # Short/glue-only expected — fall back to substring check
        return expected.lower() in peer_text.lower()
    peer_lower = peer_text.lower()
    return all(tok in peer_lower for tok in tokens)


@dataclass
class WorkflowRun:
    """Result of a single workflow execution."""

    workflow_name: str
    conversation: Conversation
    complete: WorkflowComplete
    transition_count: int


def run_workflow(
    workflow: Workflow,
    situation: Any,
    peer: PeerAdvisor,
    recorder: Optional[WorkflowRecorder] = None,
    max_turns: int = 10,
) -> WorkflowRun:
    """Execute the workflow with the given peer until it completes.

    At each transition, if a recorder is provided, capture the Igor-side
    expected_response_shape and compare to the actual peer output.

    max_turns prevents runaway conversations; hitting it aborts via
    WorkflowComplete(aborted=True).
    """
    conversation = Conversation(workflow_name=workflow.name)
    opening = workflow.opening_utterance(situation)
    conversation.add(opening)

    transition_idx = 0
    while len(conversation.utterances) < max_turns:
        peer_text = peer.respond(conversation)
        peer_utterance = WorkflowUtterance(speaker=Speaker.PEER, content=peer_text)
        conversation.add(peer_utterance)

        if recorder is not None:
            last_igor = conversation.last_igor()
            expected = (last_igor.expected_response_shape if last_igor else "") or ""
            matched = _shape_matched(expected, peer_text)
            recorder.record(
                TransitionRecord(
                    workflow_name=workflow.name,
                    conversation_id=conversation.conversation_id,
                    transition_index=transition_idx,
                    igor_state={"turn": transition_idx},
                    expected_peer_move=expected,
                    actual_peer_move=peer_text[:500],
                    matched=matched,
                )
            )
            transition_idx += 1

        result = workflow.next_utterance(conversation, peer_utterance)
        if isinstance(result, WorkflowComplete):
            return WorkflowRun(
                workflow_name=workflow.name,
                conversation=conversation,
                complete=result,
                transition_count=transition_idx,
            )
        conversation.add(result)

    # Max turns exceeded
    return WorkflowRun(
        workflow_name=workflow.name,
        conversation=conversation,
        complete=WorkflowComplete(
            output=None,
            reason=f"max_turns={max_turns} exceeded; workflow aborted",
            aborted=True,
        ),
        transition_count=transition_idx,
    )


# ── Workflow A: Experiment Design ────────────────────────────────────────────


class WorkflowA_ExperimentDesign(Workflow):
    """Igor opens: 'I'm uncertain about X. I want to design an
    experiment. Here's my current state. What probe would resolve the
    uncertainty?'

    The loop iterates until the peer has provided BOTH a concrete probe
    AND an expected observation. Exit produces a ProposedExperiment
    (from decision_blob) that can be enqueued via the experiment
    scheduler.

    The peer's answers are hypotheses per CP6 — the output carries the
    peer's proposal but it's still Igor's job to run the experiment
    and observe the real outcome. The workflow produces, doesn't
    commit.
    """

    name = "workflow_a_experiment_design"

    def opening_utterance(self, situation: Any) -> WorkflowUtterance:
        """situation is a dict with at least 'uncertainty' and optional
        'current_state' and 'what_i_tried'."""
        uncertainty = situation.get("uncertainty") or "something unclear"
        current_state = situation.get("current_state") or "(no state summary)"
        tried = situation.get("what_i_tried") or "(nothing tried yet)"
        content = (
            f"I'm uncertain about: {uncertainty}\n\n"
            f"Here's my current state: {current_state}\n\n"
            f"Here's what I've already tried: {tried}\n\n"
            "I want to design an experiment to resolve this — a concrete "
            "probe plus what outcome I should expect if my current best "
            "guess is right. What probe would you design? What should the "
            "expected observation look like?"
        )
        return WorkflowUtterance(
            speaker=Speaker.IGOR,
            content=content,
            expected_response_shape="probe + expected observation",
            metadata={
                "opening": True,
                "uncertainty": uncertainty,
            },
        )

    def next_utterance(
        self,
        conversation: Conversation,
        peer_response: WorkflowUtterance,
    ) -> WorkflowUtterance | WorkflowComplete:
        """Look for probe + expected observation in the peer response.
        Both must be present to complete; otherwise ask for whichever is
        missing.
        """
        text = peer_response.content.lower()
        has_probe = "probe:" in text or "test:" in text or "try:" in text
        has_expected = (
            "expected:" in text
            or "expect:" in text
            or "should see" in text
            or "should return" in text
        )

        if has_probe and has_expected:
            hypothesis_text = _extract_hypothesis_from_conversation(conversation)
            probe_text = _extract_field(
                peer_response.content, ["probe:", "test:", "try:"]
            )
            expected_text = _extract_field(
                peer_response.content,
                ["expected:", "expect:", "should see", "should return"],
            )
            proposed = ProposedExperiment(
                hypothesis=hypothesis_text,
                probe=probe_text or peer_response.content[:200],
                expected_observation=expected_text,
            )
            return WorkflowComplete(
                output=proposed,
                reason="peer provided probe + expected observation",
            )

        # Otherwise ask for what's missing
        missing = []
        if not has_probe:
            missing.append("a concrete probe (what specifically should I do?)")
        if not has_expected:
            missing.append(
                "the expected observation (what should I see if my guess is right?)"
            )
        content = (
            "Thanks — I need "
            + " and ".join(missing)
            + " before I can run this. Please be concrete."
        )
        return WorkflowUtterance(
            speaker=Speaker.IGOR,
            content=content,
            expected_response_shape=(
                "concrete probe + expected observation"
                if len(missing) == 2
                else missing[0]
            ),
        )

    def output_struct(self, conversation: Conversation) -> Any:
        """Rebuild the output from a completed conversation. Useful
        when a caller only kept the conversation and not the
        WorkflowComplete."""
        last_peer = conversation.last_peer()
        if not last_peer:
            return None
        hypothesis_text = _extract_hypothesis_from_conversation(conversation)
        probe_text = (
            _extract_field(last_peer.content, ["probe:", "test:", "try:"])
            or last_peer.content[:200]
        )
        expected_text = _extract_field(
            last_peer.content,
            ["expected:", "expect:", "should see", "should return"],
        )
        return ProposedExperiment(
            hypothesis=hypothesis_text,
            probe=probe_text,
            expected_observation=expected_text,
        )


# ── Extraction helpers ──────────────────────────────────────────────────────


def _extract_field(text: str, markers: list[str]) -> Optional[str]:
    """Find a marker in text and return the rest of that line (or until
    the next marker-like token). Case-insensitive."""
    if not text:
        return None
    lower = text.lower()
    for marker in markers:
        idx = lower.find(marker)
        if idx == -1:
            continue
        start = idx + len(marker)
        # Take until end-of-line or 300 chars
        rest = text[start : start + 300]
        newline = rest.find("\n")
        if newline != -1:
            rest = rest[:newline]
        return rest.strip().strip(".").strip() or None
    return None


def _extract_hypothesis_from_conversation(conversation: Conversation) -> str:
    """Pull Igor's stated uncertainty out of the opening utterance
    metadata (preferred) or the first Igor turn's content (fallback)."""
    for u in conversation.utterances:
        if u.speaker == Speaker.IGOR and u.metadata.get("opening"):
            uncertainty = u.metadata.get("uncertainty")
            if uncertainty:
                return f"resolving uncertainty about {uncertainty}"
    first_igor = next(
        (u for u in conversation.utterances if u.speaker == Speaker.IGOR),
        None,
    )
    if first_igor:
        return first_igor.content[:200]
    return "(hypothesis unknown — no Igor turns in conversation)"
