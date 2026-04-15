"""
experiment_predictor.py — T-experiment-predictor-primitive

Per-level predictor for the substrate experiment cascade. Each
CascadeLevel carries a SignaturePredictor that answers 'given this
situation, is my actor likely to succeed here?' — the walker uses this
to skip levels that know (from history) they won't match.

## Why this exists

Without predictors, the cascade always tries every level in order.
That works but doesn't compound. With predictors, a level learns
'situations with signature X have never matched for me, skip me next
time' — which is the compounding efficiency story for the shrinkage
roadmap. Moves migrate DOWNWARD through the cascade as lower levels'
predictors prove reliable.

## Biology

Cerebellar forward models are predictors for motor commands. BG
striatum is a predictor for reward. Every level in the predictive
coding hierarchy has its own predictor. When a level's predictor says
'I don't see how to reduce error here,' the level defers upward.

## Signature

MVP signature is a sorted tuple of query tokens with len >= 3. Simple,
cheap, and generalizes more than the raw query string. Future work:
embedding-based signatures, probe-shape signatures, context-aware
signatures. All can replace _signature() without touching the walker.

## CP grounding

- CP1 — initial prediction is 0.5 (maximum uncertainty), never faked
- CP2 — misses train the predictor just as hard as matches; failure is
  proportional learning, not discarded
- CP6 — the skip rule has a floor: if every level would be skipped for
  this signature, the walker disables skipping and tries them all.
  Never silently drops the whole cascade because predictors are
  over-confident.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .experiment_cascade import CascadeSituation

logger = logging.getLogger(__name__)


# ── Tuning knobs ─────────────────────────────────────────────────────────────

INITIAL_CONFIDENCE: float = 0.5
"""Starting prediction for unseen signatures — maximally uncertain."""

SKIP_THRESHOLD: float = 0.2
"""Walker skips a level when its predictor confidence is below this."""

MIN_TOKEN_LEN: int = 3
"""Tokens shorter than this are ignored when building signatures."""


# ── Signature ────────────────────────────────────────────────────────────────


def _signature(situation: "CascadeSituation") -> tuple[str, ...]:
    """Canonical signature for predictor memory.

    MVP: sorted tuple of query tokens with len >= MIN_TOKEN_LEN,
    lowercased. Deterministic, cheap, generalizes across phrasing.
    """
    if not situation.query:
        return ()
    tokens = {t.lower() for t in situation.query.split() if len(t) >= MIN_TOKEN_LEN}
    return tuple(sorted(tokens))


# ── SignaturePredictor ───────────────────────────────────────────────────────


class SignaturePredictor:
    """Tracks hit/miss per signature. Hebbian update rule.

    State is in-memory for MVP (per-process, resets on restart). A
    future ticket can persist it into experiment_queue rows or a new
    metadata stack on the level's tracking node — when it does, this
    class's predict/train API stays the same and the storage swap is
    local.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, ...], int] = defaultdict(int)
        self._misses: dict[tuple[str, ...], int] = defaultdict(int)

    def predict(self, situation: "CascadeSituation") -> float:
        """Return confidence in [0.0, 1.0] that this level will match
        on the given situation. Returns INITIAL_CONFIDENCE for unseen
        signatures.
        """
        sig = _signature(situation)
        hits = self._hits.get(sig, 0)
        misses = self._misses.get(sig, 0)
        total = hits + misses
        if total == 0:
            return INITIAL_CONFIDENCE
        return hits / total

    def train(self, situation: "CascadeSituation", matched: bool) -> None:
        """Update the predictor with an observed outcome. matched=True
        means the level resolved the situation (MATCHED / LEVERAGED /
        ESCALATE-as-success for the escalation level); False means
        EXHAUSTED."""
        sig = _signature(situation)
        if matched:
            self._hits[sig] += 1
        else:
            self._misses[sig] += 1

    def overall_hit_rate(self) -> float:
        """Aggregate hit rate across all signatures — graduation signal."""
        total_hits = sum(self._hits.values())
        total_misses = sum(self._misses.values())
        total = total_hits + total_misses
        if total == 0:
            return INITIAL_CONFIDENCE
        return total_hits / total

    def stats(self) -> dict:
        """Summary for audit / graduation reporting."""
        all_sigs = set(self._hits.keys()) | set(self._misses.keys())
        return {
            "signatures_tracked": len(all_sigs),
            "total_hits": sum(self._hits.values()),
            "total_misses": sum(self._misses.values()),
            "overall_hit_rate": self.overall_hit_rate(),
        }

    def reset(self) -> None:
        """Wipe predictor memory. Test / debug utility."""
        self._hits.clear()
        self._misses.clear()
