"""
experiment.py — T-experiment-primitive-schema

Data model for Igor's strategy-2 substrate. An experiment is a linked
quartet: Hypothesis (proposition) → Probe (action) → Observation
(what happened) → Update (what changes).

This module is the schema only. The scheduler, outcome feedback,
cockpit integration, and CP-check sub-slices all consume it.

## Axiomatic grounding (see CP1-CP6, T-architecture-core-principles)

- **CP1 "I don't know"** — Hypothesis.confidence is a required field,
  never assumed. Observation outcomes include `inconclusive` as a
  first-class state, not a failure. Uncertainty is representable
  throughout.
- **CP2 "FAIL = Further Advance In Learning"** — failed Observations
  trigger Update objects that feed learning. Failure is a state, not
  an error. The Update layer exists precisely to capture the learning
  from any outcome, including mismatches.
- **CP3 "There's always a why"** — every Hypothesis has a causal
  statement. Every Probe specifies what it expects. Every Observation
  relates back to the hypothesis it tested. No silent mutations.
- **CP6 "Build safety as we go"** — no experiment can update Igor's
  state without passing through the Update layer. Direct mutation
  from hypothesis-bearing sources is forbidden.

## Integration with decision_blob.py

The decision_blob module has a lightweight `ProposedExperiment`
dataclass with (hypothesis, probe, expected_observation, cost_estimate).
That's the 'draft' state — a suggestion from the reasoning layer that
an experiment should happen. This module's full `Experiment` dataclass
is the complete lifecycle around that seed.

Lifecycle:
  ProposedExperiment (in decision blob) → Experiment.PROPOSED →
  Experiment.RUNNING → Experiment.OBSERVED → Experiment.UPDATED

Terminal states: UPDATED (success), ABORTED (cancelled mid-lifecycle).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# ── Enums ────────────────────────────────────────────────────────────────────


class ExperimentStatus(str, Enum):
    """Lifecycle state of an experiment."""

    PROPOSED = "proposed"
    RUNNING = "running"
    OBSERVED = "observed"
    UPDATED = "updated"
    ABORTED = "aborted"


class Outcome(str, Enum):
    """What the observation reported about the hypothesis."""

    MATCH = "match"
    MISMATCH = "mismatch"
    PARTIAL = "partial"
    INCONCLUSIVE = "inconclusive"


class ProbeKind(str, Enum):
    """What kind of action the probe performs."""

    MEMORY_QUERY = "memory_query"
    TOOL_CALL = "tool_call"
    HABIT_DRYRUN = "habit_dryrun"
    CHANNEL_SEND = "channel_send"
    DB_QUERY = "db_query"
    SIM_TURN = "sim_turn"


# ── Allowed state transitions (CP-consistent lifecycle) ──────────────────────


_ALLOWED_TRANSITIONS: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.PROPOSED: {ExperimentStatus.RUNNING, ExperimentStatus.ABORTED},
    ExperimentStatus.RUNNING: {ExperimentStatus.OBSERVED, ExperimentStatus.ABORTED},
    ExperimentStatus.OBSERVED: {ExperimentStatus.UPDATED, ExperimentStatus.ABORTED},
    ExperimentStatus.UPDATED: set(),  # terminal
    ExperimentStatus.ABORTED: set(),  # terminal
}


# ── id generator ─────────────────────────────────────────────────────────────


def _new_experiment_id() -> str:
    """Canonical D256 timestamp format: yyyymmdd.hhmmssuuuuuu.xxxxxxx.

    Matches T-architecture-core-principles timestamp_id_format_uniform.
    Short hex tag disambiguates within a microsecond.
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S") + f"{now.microsecond:06d}"
    suffix = uuid.uuid4().hex[:7]
    return f"{date_part}.{time_part}.{suffix}"


# ── Hypothesis ───────────────────────────────────────────────────────────────


@dataclass
class Hypothesis:
    """A structured proposition to be tested.

    CP1: confidence is always representable. CP3: statement must have
    a causal story (the 'why' the hypothesis is being made).
    """

    statement: str
    """Human-readable causal proposition, e.g. 'input X should produce outcome Y'."""

    source: str
    """Where the hypothesis came from: 'substrate' | 'reasoning_llm' |
    'external' | 'boredom_escalation' | 'self_audit' | etc. CP6 tracks
    trust lineage via source."""

    confidence: float = 0.0
    """Prior confidence in the hypothesis before testing (0.0-1.0)."""

    cp_constraints: dict[str, Any] = field(default_factory=dict)
    """Any CP-axiom constraints this hypothesis must honor. For example:
    {'must_preserve': ['cp6'], 'rejects_on': 'mismatch'}."""

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError(
                "Hypothesis.statement must be non-empty (CP3: causal story required)"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Hypothesis.confidence must be in [0.0, 1.0], got {self.confidence}"
            )


# ── Probe ────────────────────────────────────────────────────────────────────


@dataclass
class Probe:
    """Concrete action that exercises a hypothesis.

    CP3: must specify what it expects. Without expected_shape the
    observation layer has no way to interpret the result.
    """

    kind: ProbeKind
    target: str
    """What to query, call, send to. Format depends on kind: tool name,
    memory id, habit id, channel name, SQL, etc."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Arguments to pass to the probe action."""

    expected_shape: Optional[str] = None
    """Human-readable description of what a matching observation would
    look like. CP3: if None, the probe doesn't know what it's testing."""

    cost_estimate: Optional[str] = None
    """Pre-run estimate of resource cost (tokens, latency, risk).
    Feeds the scheduler."""

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = ProbeKind(self.kind)
        if not self.target.strip():
            raise ValueError("Probe.target must be non-empty")


# ── Observation ──────────────────────────────────────────────────────────────


@dataclass
class Observation:
    """What the probe actually produced.

    CP2: failure outcomes are learning, not errors — `mismatch`,
    `inconclusive`, and `partial` are all first-class.
    """

    outcome: Outcome
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    cost: dict[str, Any] = field(default_factory=dict)
    """Actual cost incurred: tokens used, latency ms, etc."""

    notes: Optional[str] = None
    """Human-readable description of what happened. For when data
    alone doesn't tell the story."""

    def __post_init__(self) -> None:
        if isinstance(self.outcome, str):
            self.outcome = Outcome(self.outcome)


# ── Update ───────────────────────────────────────────────────────────────────


@dataclass
class Update:
    """What changes in Igor's engrams as a result of the observation.

    CP6: every state change from an experiment MUST flow through this
    object. Direct mutation bypassing Update is forbidden — the layer
    exists specifically to audit and contain the effects of strategy-2
    work.
    """

    trail_edge_changes: list[dict[str, Any]] = field(default_factory=list)
    """Hebbian strengthening/weakening of interpretive_edges. Each entry
    describes a (from, to, delta) change."""

    memory_accretions: list[str] = field(default_factory=list)
    """New memory ids created as a result of the experiment."""

    inhibitor_weight_deltas: dict[str, float] = field(default_factory=dict)
    """Selection-layer bias adjustments from the experiment outcome."""

    goal_state_transitions: list[dict[str, Any]] = field(default_factory=list)
    """Goal facia state changes (progress, blocked, completed, etc.)."""

    reason: str = ""
    """Human-readable explanation of why these updates were applied.
    CP3: the 'why' of the update."""


# ── Experiment (the full linked quartet) ─────────────────────────────────────


@dataclass
class Experiment:
    """Complete experiment lifecycle: hypothesis + probe + observation + update.

    Construction rules:
      - hypothesis and probe are required at PROPOSED state
      - observation populated during RUNNING → OBSERVED transition
      - update populated during OBSERVED → UPDATED transition
      - experiment_id auto-generated in D256 format if not provided
    """

    hypothesis: Hypothesis
    probe: Probe
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    observation: Optional[Observation] = None
    update: Optional[Update] = None
    experiment_id: str = field(default_factory=_new_experiment_id)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    parent_blob_id: Optional[str] = None
    """If this experiment was proposed by a DecisionBlob, the blob_id
    that proposed it. Links experiments back to the reasoning context."""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = ExperimentStatus(self.status)

    # ── Lifecycle transitions ────────────────────────────────────────────────

    def advance(self, new_status: ExperimentStatus | str) -> None:
        """Advance to a new lifecycle state. Raises ValueError on invalid transition."""
        if isinstance(new_status, str):
            new_status = ExperimentStatus(new_status)
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition {self.status.value} → {new_status.value}. "
                f"Allowed: {sorted(s.value for s in allowed) or '[terminal]'}"
            )
        self.status = new_status

    def record_observation(self, observation: Observation) -> None:
        """Attach an observation and advance to OBSERVED state.

        Requires current status = RUNNING.
        """
        if self.status != ExperimentStatus.RUNNING:
            raise ValueError(
                f"record_observation requires status=RUNNING, got {self.status.value}"
            )
        self.observation = observation
        self.advance(ExperimentStatus.OBSERVED)

    def apply_update(self, update: Update) -> None:
        """Attach an update and advance to UPDATED (terminal) state.

        Requires current status = OBSERVED.
        """
        if self.status != ExperimentStatus.OBSERVED:
            raise ValueError(
                f"apply_update requires status=OBSERVED, got {self.status.value}"
            )
        if not update.reason.strip():
            raise ValueError("Update.reason must be non-empty (CP3: why is required)")
        self.update = update
        self.advance(ExperimentStatus.UPDATED)

    def abort(self, reason: str = "") -> None:
        """Terminal-abort the experiment from any non-terminal state.

        For scheduler timeouts, priority shifts, or explicit cancellation.
        """
        if self.status in (ExperimentStatus.UPDATED, ExperimentStatus.ABORTED):
            raise ValueError(f"cannot abort from terminal state {self.status.value}")
        self.status = ExperimentStatus.ABORTED

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["hypothesis"] = asdict(self.hypothesis)
        d["probe"] = asdict(self.probe)
        d["probe"]["kind"] = self.probe.kind.value
        if self.observation is not None:
            d["observation"] = asdict(self.observation)
            d["observation"]["outcome"] = self.observation.outcome.value
        if self.update is not None:
            d["update"] = asdict(self.update)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Experiment":
        data = dict(data)
        data["hypothesis"] = Hypothesis(**data["hypothesis"])
        probe_data = dict(data["probe"])
        if isinstance(probe_data.get("kind"), str):
            probe_data["kind"] = ProbeKind(probe_data["kind"])
        data["probe"] = Probe(**probe_data)
        if data.get("observation") is not None:
            obs = dict(data["observation"])
            if isinstance(obs.get("outcome"), str):
                obs["outcome"] = Outcome(obs["outcome"])
            data["observation"] = Observation(**obs)
        if data.get("update") is not None:
            data["update"] = Update(**data["update"])
        if isinstance(data.get("status"), str):
            data["status"] = ExperimentStatus(data["status"])
        return cls(**data)

    @classmethod
    def from_json(cls, text: str) -> "Experiment":
        return cls.from_dict(json.loads(text))


# ── Bridge to decision_blob.ProposedExperiment ───────────────────────────────


def from_proposed(
    proposed,  # decision_blob.ProposedExperiment
    *,
    source: str = "substrate",
    confidence: float = 0.0,
    probe_kind: ProbeKind | str = ProbeKind.TOOL_CALL,
    probe_target: str = "",
    probe_payload: Optional[dict[str, Any]] = None,
    parent_blob_id: Optional[str] = None,
) -> Experiment:
    """Convert a decision_blob.ProposedExperiment (lightweight draft) into
    a full Experiment lifecycle object in PROPOSED state.

    The ProposedExperiment carries the hypothesis + probe intent + expected
    observation. We fill in the probe kind/target/payload and create the
    full quartet ready for scheduling.
    """
    if isinstance(probe_kind, str):
        probe_kind = ProbeKind(probe_kind)

    return Experiment(
        hypothesis=Hypothesis(
            statement=proposed.hypothesis,
            source=source,
            confidence=confidence,
        ),
        probe=Probe(
            kind=probe_kind,
            target=probe_target or proposed.probe,
            payload=probe_payload or {},
            expected_shape=proposed.expected_observation,
            cost_estimate=proposed.cost_estimate,
        ),
        parent_blob_id=parent_blob_id,
    )
