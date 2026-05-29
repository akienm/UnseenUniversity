"""
habit_tiebreaker.py — Rule-based habit competition resolver.

Compiled inference replacement for IgorMain._try_habit_tiebreaker().
Uses word-overlap scoring to pick a winner when the answer is unambiguous.
Falls back gracefully (returns None) when candidates are too close to call.
"""

from __future__ import annotations

import re

_MIN_OVERLAP = 0.15  # below this, no candidate is strong enough
_DOMINANCE_RATIO = 2.0  # winner must score this many times higher than second-best


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"\b\w{4,}\b", text.lower()))


def select_by_overlap(user_input: str, candidates: list[dict]) -> str | None:
    """
    Deterministically resolve habit competition using word-overlap scoring.

    Args:
        user_input: The user's message that triggered habit matching.
        candidates: List of {"id": str, "score": float, "narrative": str}.
                    `score` is the basal_ganglia match score (used as tiebreaker
                    when overlap is equal; not the primary signal here).

    Returns:
        habit_id of the clear winner, or None when the decision is ambiguous
        (caller should invoke LLM arbitration).

    A 'clear winner' requires:
    - Best overlap >= MIN_OVERLAP
    - Best overlap >= DOMINANCE_RATIO × second-best overlap (or only one candidate)
    """
    if not candidates or not user_input:
        return None

    user_words = _word_set(user_input)
    if not user_words:
        return None

    scored: list[tuple[float, float, str]] = []  # (overlap, bg_score, id)
    for c in candidates:
        habit_words = _word_set(c.get("narrative", ""))
        if habit_words:
            overlap = len(user_words & habit_words) / len(habit_words)
        else:
            overlap = 0.0
        scored.append((overlap, float(c.get("score", 0.0)), c["id"]))

    scored.sort(reverse=True)  # primary: overlap, secondary: bg_score
    best_overlap, _, best_id = scored[0]

    if best_overlap < _MIN_OVERLAP:
        return None

    if len(scored) == 1:
        return best_id

    second_overlap = scored[1][0]
    if second_overlap > 0 and (best_overlap / second_overlap) < _DOMINANCE_RATIO:
        return None  # too close to call

    return best_id
