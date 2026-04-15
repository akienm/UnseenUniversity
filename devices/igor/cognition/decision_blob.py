"""
decision_blob.py — T-decision-blob-schema

The shared contract between Igor's substrate, the reasoning LLM (when
called), the experiment primitive (when testing hypotheses), and the
voice actor (when producing output). Every cognitive layer between
"substrate has done its salience competition" and "voice emits prose"
speaks this shape.

## Axiomatic grounding (see theigors/architecture/principles and CP1-CP6)

- **CP1 "I don't know"** — selected_action may be None. Confidence may
  be low. A low-confidence blob MUST NOT commit; it must produce an
  experiment or defer. Uncertainty is always representable.
- **CP2 "FAIL = Further Advance In Learning"** — failed decisions aren't
  discarded. The outcome field (populated post-execution) feeds back to
  trail training so every decision is a learning opportunity.
- **CP3 "There's always a why"** — cp_validation.cp3_has_why is a
  required field. Every decision either has a causal story or
  explicitly confesses its absence (never silently lacks one).
- **CP6 "Build safety as we go"** — any field sourced from an untested
  LLM output lands in `hypothesis`, NOT `selected_action`. Committing
  to a hypothesis without testing it is a CP6 violation.

## Design notes

The schema is deliberately opinionated about what goes where:
  - LLM output → hypothesis (never selected_action)
  - Substrate output → selected_action (after the substrate picked a
    confident winner) or proposed_experiment (if substrate declined to
    commit and wants to probe instead)
  - Voice actor reads selected_action and register_hints; it is NOT
    responsible for re-deciding

The blob is designed to be stored — blob_id follows D256 timestamp
format (yyyymmdd.hhmmssuuuuuu.xxxxxxx per T-architecture-core-principles
timestamp_id_format_uniform principle). Every decision is queryable
after the fact for diagnostic and training purposes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# ── Enums ────────────────────────────────────────────────────────────────────


class Intent(str, Enum):
    """What kind of move this decision represents."""

    ANSWER = "answer"
    ASK = "ask"
    OBSERVE = "observe"
    ADOPT_GOAL = "adopt_goal"
    DECLINE = "decline"
    DEFER = "defer"
    EXPERIMENT = "experiment"


# ── Timestamp id format (D256 / T-architecture-core-principles) ──────────────


def _new_blob_id() -> str:
    """Generate a new blob_id in canonical yyyymmdd.hhmmssuuuuuu.xxxxxxx format.

    The suffix uses a short random hex tag rather than a commit hash so
    multiple blobs within the same microsecond can be distinguished.
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S") + f"{now.microsecond:06d}"
    suffix = uuid.uuid4().hex[:7]
    return f"{date_part}.{time_part}.{suffix}"


# ── CP validation ────────────────────────────────────────────────────────────


@dataclass
class CPValidation:
    """Per-CP-axiom compliance flags on a decision blob.

    Every decision must carry these so audit checks can validate
    CP-consistency. Missing fields signal that the substrate didn't
    think about the axiom — audit should flag that as incomplete.
    """

    cp1_provisional: bool = True
    """CP1: decision is acknowledged as current best guess, not certainty."""

    cp2_failure_captures_learning: bool = True
    """CP2: if this decision fails, the outcome will feed back to learning."""

    cp3_has_why: Optional[str] = None
    """CP3: human-readable causal story for the decision. None = absence explicitly confessed."""

    cp6_sources_verified: list[str] = field(default_factory=list)
    """CP6: list of sources that have been experiment-tested. Any unverified
    source (including LLM output) MUST NOT feed selected_action directly."""

    def is_complete(self) -> bool:
        """Return True if every CP flag has been deliberately set."""
        return self.cp3_has_why is not None

    def blocks_commitment(self) -> list[str]:
        """Return list of CP violations that block committing to selected_action.

        Empty list = safe to commit. Non-empty = must either experiment
        first or defer.
        """
        blocks: list[str] = []
        if not self.cp1_provisional:
            blocks.append("CP1: decision claims non-provisional certainty")
        if self.cp3_has_why is None:
            blocks.append("CP3: no causal story; why is absent and unconfessed")
        return blocks


# ── Considered alternatives ──────────────────────────────────────────────────


@dataclass
class Alternative:
    """A candidate that competed at selection time and lost.

    Surfacing losers with their scores makes the selection bias legible
    (CP1 — the decision isn't presented as the only possible answer).
    """

    candidate: str
    score: float
    reason: str = ""


# ── Proposed experiment (strategy 2) ─────────────────────────────────────────


@dataclass
class ProposedExperiment:
    """An experiment to run if committing isn't safe yet.

    Consumed by T-experiment-primitive. The experiment is the strategy-2
    move: generate the data you don't have rather than defer or commit
    on faith.
    """

    hypothesis: str
    probe: str
    expected_observation: Optional[str] = None
    cost_estimate: Optional[str] = None


# ── Provenance ───────────────────────────────────────────────────────────────


@dataclass
class Provenance:
    """Required metadata about who made this decision and from what.

    CP6 demands verifiability — you cannot build safety on untraceable
    decisions. Every blob carries this.
    """

    maker: str  # "substrate" | "reasoning_llm" | "experiment" | "voice" | etc
    inputs: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None


# ── The blob ─────────────────────────────────────────────────────────────────


@dataclass
class DecisionBlob:
    """Structured cognitive decision, passed between layers.

    Construction rules enforced in __post_init__:
      - blob_id auto-generated if not provided
      - cp_validation auto-created if not provided
      - Either selected_action OR proposed_experiment should be present
        (not both committed; blobs carrying only a hypothesis have
        neither populated, indicating that reasoning is still open)
    """

    intent: Intent
    selected_action: Optional[str] = None
    hypothesis: Optional[str] = None
    considered_alternatives: list[Alternative] = field(default_factory=list)
    importance_weights: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    cp_validation: CPValidation = field(default_factory=CPValidation)
    register_hints: dict[str, Any] = field(default_factory=dict)
    proposed_experiment: Optional[ProposedExperiment] = None
    provenance: Optional[Provenance] = None
    trail_id: Optional[str] = None
    blob_id: str = field(default_factory=_new_blob_id)
    outcome: Optional[str] = None
    """Post-execution outcome capture. Populated after voice + world contact.
    Feeds back to trail training per CP2."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.provenance is None:
            raise ValueError(
                "DecisionBlob requires provenance — CP6 demands verifiability"
            )
        if isinstance(self.intent, str):
            self.intent = Intent(self.intent)

    # ── CP-consistency guards ────────────────────────────────────────────────

    def can_commit(self) -> tuple[bool, list[str]]:
        """Return (safe_to_commit, list_of_reasons_why_not).

        Safe to commit = we can emit selected_action to the voice actor
        without violating a CP axiom. Unsafe = must experiment or defer.
        """
        reasons: list[str] = []

        cp_blocks = self.cp_validation.blocks_commitment()
        reasons.extend(cp_blocks)

        if self.selected_action is None:
            reasons.append("selected_action is None — nothing to commit")
        if self.hypothesis is not None and self.selected_action is None:
            reasons.append(
                "hypothesis present but selected_action None — must experiment "
                "on the hypothesis before committing (CP6)"
            )
        if self.confidence < 0.5:
            reasons.append(
                f"confidence {self.confidence:.2f} < 0.5 — too low to commit "
                "without more data (CP1 — should probably experiment or defer)"
            )

        return (not reasons, reasons)

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict. Intent enum becomes its value string."""
        d = asdict(self)
        d["intent"] = (
            self.intent.value if isinstance(self.intent, Intent) else self.intent
        )
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionBlob":
        """Reconstruct a DecisionBlob from a dict (e.g. loaded from memory metadata)."""
        data = dict(data)

        if "cp_validation" in data and isinstance(data["cp_validation"], dict):
            data["cp_validation"] = CPValidation(**data["cp_validation"])
        if "considered_alternatives" in data and data["considered_alternatives"]:
            data["considered_alternatives"] = [
                Alternative(**a) if isinstance(a, dict) else a
                for a in data["considered_alternatives"]
            ]
        if "proposed_experiment" in data and isinstance(
            data["proposed_experiment"], dict
        ):
            data["proposed_experiment"] = ProposedExperiment(
                **data["proposed_experiment"]
            )
        if "provenance" in data and isinstance(data["provenance"], dict):
            data["provenance"] = Provenance(**data["provenance"])
        if "intent" in data and isinstance(data["intent"], str):
            data["intent"] = Intent(data["intent"])
        return cls(**data)

    @classmethod
    def from_json(cls, text: str) -> "DecisionBlob":
        return cls.from_dict(json.loads(text))


# ── Helper: construct a half-populated blob from substrate output ────────────


def from_substrate(
    *,
    intent: Intent | str,
    considered: list[Alternative] | None = None,
    weights: dict[str, float] | None = None,
    confidence: float = 0.0,
    thread_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    why: Optional[str] = None,
    verified_sources: Optional[list[str]] = None,
    trail_id: Optional[str] = None,
) -> DecisionBlob:
    """Build a decision blob from the substrate's per-turn work.

    Reduces boilerplate at every substrate → reasoning handoff site.
    Returns a blob with provenance=substrate, CP validation prepopulated
    where possible, and no selected_action yet (reasoning/experiment/
    voice may commit later).
    """
    return DecisionBlob(
        intent=intent if isinstance(intent, Intent) else Intent(intent),
        considered_alternatives=considered or [],
        importance_weights=weights or {},
        confidence=confidence,
        cp_validation=CPValidation(
            cp1_provisional=True,
            cp2_failure_captures_learning=True,
            cp3_has_why=why,
            cp6_sources_verified=verified_sources or [],
        ),
        provenance=Provenance(
            maker="substrate",
            inputs=[],
            thread_id=thread_id,
            turn_id=turn_id,
        ),
        trail_id=trail_id,
    )
