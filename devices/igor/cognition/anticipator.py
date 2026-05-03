"""anticipator.py — Anticipation primitive (T-anticipation-primitive slice 1).

Forward-looking pull-signal distinct from backward-looking bliss/valence. The
"want" Igor is missing (memory/project_missing_want_primitive.md): a predictor
that biases action selection BEFORE the outcome is observed, then learns from
the surprise (RPE) once the actual outcome is known.

Slice 1 ships the substrate — dataclass, predictor stub, active-set bus, RPE
register_outcome path. Plug-points (Pursuit-adoption emit, action-selection
read, milieu update from RPE) come in subsequent slices.

The existing `anticipation.py` is a narrower closure-valence-for-ticket-
selection helper (T-anticipation-pull, shipped). Kept distinct: that file is
sort-key heuristics; this file is the general predictor primitive.

Shape:
  Anticipation       — dataclass: referent + predicted_delta + confidence
  Anticipator        — .predict(referent) → Anticipation
                       .register_outcome(ant, actual_delta) → rpe (signed)
  AnticipationBus    — module-level active-set; top_k() biased read; settle()

Slice 1 prediction strategy is intentionally simple — rolling mean per
referent_type, default delta 0.0, confidence rises with sample count. Real
RL training is a later slice; today's stub makes that swap a one-class change.

Updated 2026-05-02.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from threading import RLock

from ..igor_base import IgorBase

# ── Anticipation dataclass ─────────────────────────────────────────────────


@dataclass
class Anticipation:
    """A single predicted-valence-delta toward a referent.

    referent_id    — opaque id of the thing being anticipated (ticket id,
                     pursuit id, action name — whatever the caller uses).
    referent_type  — string tag for grouping in the predictor (e.g.
                     "pursuit", "ticket", "workflow_step").
    predicted_delta— signed predicted change in valence/reward when the
                     referent resolves. Positive = "I want this," negative
                     = "I'd prefer to avoid."
    confidence     — 0.0 to 1.0 — how much weight downstream consumers
                     should give this prediction.
    created_at     — wall-clock seconds at construction; consumers can age
                     out stale predictions.
    id             — unique handle so register_outcome can match anticipation
                     to outcome without ambiguity when multiple are live.
    """

    referent_id: str
    referent_type: str
    predicted_delta: float
    confidence: float
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: f"ant-{uuid.uuid4().hex[:12]}")


# ── Anticipator (predictor + RPE update) ───────────────────────────────────


class Anticipator(IgorBase):
    """Simple per-referent_type predictor with RPE-driven learning.

    Slice 1 model: rolling mean of observed deltas per referent_type. Each
    register_outcome() call records the actual_delta and recomputes the
    mean. Confidence rises asymptotically with sample count (1 - 1/(n+1)).

    The interface is the same shape later RL implementations will take —
    .predict and .register_outcome. The internals are a swap point.
    """

    def __init__(self) -> None:
        super().__init__()
        self._sums: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._lock = RLock()

    def predict(self, referent_id: str, referent_type: str) -> Anticipation:
        """Build an Anticipation for the given referent.

        Returns predicted_delta = rolling mean for this referent_type
        (or 0.0 when the type has no samples yet) and a confidence that
        grows with sample count.
        """
        with self._lock:
            n = self._counts.get(referent_type, 0)
            mean = (self._sums.get(referent_type, 0.0) / n) if n else 0.0
            confidence = 1.0 - 1.0 / (n + 1)
        return Anticipation(
            referent_id=referent_id,
            referent_type=referent_type,
            predicted_delta=mean,
            confidence=confidence,
        )

    def register_outcome(self, ant: Anticipation, actual_delta: float) -> float:
        """Record an actual outcome and update the predictor.

        Returns the RPE — actual_delta minus predicted_delta. Signed:
        positive = better than expected (the dopamine surge); negative =
        worse. Caller can route the RPE elsewhere (milieu valence push,
        downstream learning).
        """
        with self._lock:
            self._sums[ant.referent_type] = (
                self._sums.get(ant.referent_type, 0.0) + actual_delta
            )
            self._counts[ant.referent_type] = self._counts.get(ant.referent_type, 0) + 1
        return actual_delta - ant.predicted_delta


# ── AnticipationBus (active-set with biased read) ──────────────────────────


class AnticipationBus(IgorBase):
    """Module-level active-set of live anticipations.

    Producers push (Pursuit adoption, plan-step entry); consumers read
    via top_k() to bias action selection toward high predicted_delta.
    settle() removes an anticipation by id once its outcome lands.

    Not persisted — anticipations are working-memory shaped, not a DB
    table. Survives within-process only.
    """

    def __init__(self) -> None:
        super().__init__()
        self._active: dict[str, Anticipation] = {}
        self._lock = RLock()

    def push(self, ant: Anticipation) -> None:
        """Add an anticipation to the active set. Idempotent on id."""
        with self._lock:
            self._active[ant.id] = ant

    def top_k(self, k: int = 5) -> list[Anticipation]:
        """Return up to k active anticipations sorted by predicted_delta
        weighted by confidence (descending). Consumers use this to bias
        toward high-want referents."""
        with self._lock:
            ranked = sorted(
                self._active.values(),
                key=lambda a: a.predicted_delta * a.confidence,
                reverse=True,
            )
        return ranked[:k]

    def settle(self, ant_id: str) -> Anticipation | None:
        """Remove and return the anticipation matching ant_id. Returns
        None when no match — caller decides whether that's worth a
        warning."""
        with self._lock:
            return self._active.pop(ant_id, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)


# ── Module-level singleton (consumers reach via this) ──────────────────────

_ANTICIPATOR: Anticipator | None = None
_BUS: AnticipationBus | None = None


def get_anticipator() -> Anticipator:
    global _ANTICIPATOR
    if _ANTICIPATOR is None:
        _ANTICIPATOR = Anticipator()
    return _ANTICIPATOR


def get_bus() -> AnticipationBus:
    global _BUS
    if _BUS is None:
        _BUS = AnticipationBus()
    return _BUS


def reset_for_test() -> None:
    """Test helper — clear the singletons so tests don't share state."""
    global _ANTICIPATOR, _BUS
    _ANTICIPATOR = None
    _BUS = None


# ── Slice 3 — selection-bias hookpoint for action selection ─────────────────


def _bias_weight() -> float:
    """IGOR_ANTICIPATION_BIAS_WEIGHT — how strongly anticipation tilts
    selection scores. Default 0.1 (gentle — anticipation directional, not
    overriding). Set 0 to disable the bias entirely."""
    try:
        return float(os.getenv("IGOR_ANTICIPATION_BIAS_WEIGHT", "0.1"))
    except ValueError:
        return 0.1


def anticipation_bias_for_referent(referent_id: str) -> float:
    """Return the score-bonus to add when selecting toward `referent_id`.

    Computed as `predicted_delta * confidence * IGOR_ANTICIPATION_BIAS_WEIGHT`
    if the referent is currently anticipated on the bus; 0.0 otherwise.

    Consumers (basal_ganglia habit selection, plan_traverse waypoint
    pick) call this with the candidate's referent — if no live
    anticipation exists for it, the candidate is unaffected. The bonus
    is intentionally gentle so anticipation steers but doesn't override
    deterministic selection rules.
    """
    weight = _bias_weight()
    if weight == 0.0:
        return 0.0
    bus = get_bus()
    # Linear scan: active set is small (≤ a few dozen typically). If this
    # ever becomes hot, index by referent_id.
    for ant in bus.top_k(k=bus.active_count()):
        if ant.referent_id == referent_id:
            return ant.predicted_delta * ant.confidence * weight
    return 0.0
