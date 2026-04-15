"""
engineered_failure.py — T-engineered-failure-experiments

Urgency-inverts-risk-appetite policy for the substrate experiment
cascade. Under high stakes, the walker prefers probes whose outcome
we can LEAST predict — because those probes have the highest
information-per-attempt ratio.

From Akien 2026-04-15 loop description: 'if the thing is urgent, then
i will try less likely experiments on the off chance that their
failures will teach me something useful. and then i will do them in
such a way to try and insure they don't fail. because that's how i'll
learn the most.'

## Biology

Dopaminergic novelty bonus is dialed UP under stress in mammals —
exploration rate increases, not decreases. Biology endorses this as
correct policy, not panic. The felt experience is 'trying weird things
when nothing else works.'

## Information gain, Shannon-style

`gain = 1 - |P(match) - 0.5| * 2`

  - P(match) = 0.5 → gain = 1.0 (maximum disambiguation)
  - P(match) = 0.0 or 1.0 → gain = 0.0 (outcome already known)
  - Linear interpolation between

Under high stakes, the walker sorts active levels by info_gain
descending and walks the reordered list. Under normal stakes, walker
order is preserved (insertion order = cheapest first).

## CP grounding

- CP2 — failure is learning; this is the CP2 move operationalized as a
  decision policy under pressure, not retroactive rationalization
- CP6 — safeguards constrain the failure space. The engineered-failure
  flag on a probe tells the level to capture richer observation data so
  any failure produces maximum learning. Levels mark probes
  `no_engineered_failure=True` to opt out of the policy (destructive
  actions, external API calls).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .experiment_cascade import CascadeLevel, CascadeSituation

logger = logging.getLogger(__name__)


STAKES_THRESHOLD: float = 0.7
"""Walker switches to engineered-failure policy when situation.stakes
>= this. Tunable; start conservative. Lower = more exploration, higher
= more exploitation."""


def information_gain(predictor_confidence: float) -> float:
    """Shannon-style information gain for a single probe whose predictor
    gives this confidence. Peaks at 0.5 (maximally uncertain), drops to
    0.0 at either extreme (outcome already known).

    Returns a value in [0.0, 1.0].
    """
    # Clamp to [0, 1] defensively — predictors should stay in range but
    # we don't want a rogue value to produce negative gains.
    p = max(0.0, min(1.0, predictor_confidence))
    return 1.0 - abs(p - 0.5) * 2.0


def is_high_stakes(situation: "CascadeSituation") -> bool:
    """True when the walker should engage engineered-failure policy."""
    return situation.stakes >= STAKES_THRESHOLD


def sort_by_information_gain(
    levels: list["CascadeLevel"],
    situation: "CascadeSituation",
) -> list["CascadeLevel"]:
    """Return a copy of `levels` sorted by descending information gain
    for the given situation. Levels whose predictor raises default to
    info_gain=1.0 (maximally informative / safe keep).
    """

    def _gain(level: "CascadeLevel") -> float:
        try:
            confidence = level.predict(situation)
        except Exception as exc:
            logger.debug(
                "info_gain predictor for %s raised: %s — defaulting to 1.0",
                level.name,
                exc,
            )
            return 1.0
        return information_gain(confidence)

    # Sort by gain desc, then by original order for stability (Python
    # sort is stable, so pairing with enumerate preserves ties.)
    indexed = [(idx, _gain(lv), lv) for idx, lv in enumerate(levels)]
    indexed.sort(key=lambda x: (-x[1], x[0]))
    return [entry[2] for entry in indexed]
