"""
Basal Ganglia — parallel habit scoring with lateral inhibition.

Replaces _find_habit() (first-match-wins) with a proper scoring model:

  score = trigger_score          # 1.0 if trigger in input, else 0.0 (required)
        + keyword_bonus          # 0.0–0.15 (narrative keyword overlap)
        + activation_bonus       # 0.0–0.15 (experienced habits slightly preferred)
        + inertia_bonus          # 0.0–0.10 (stable habits preferred over new)
        + valence_bonus          # 0.0–0.10 (positive-valence habits slightly preferred)

All habits scored in parallel; winner is max(scores). Tiebreak by activation_count.
Threshold is modulated by milieu state: high arousal → lower threshold (more reactive).

Natural-language habit-compilation phrases bypass scoring: they return
PROC_HABIT_COMPILER immediately at confidence 0.95.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Memory
    from .milieu import MilieuState


# ── Word graph integration ─────────────────────────────────────────────────────
# Injected at boot by main.py. None until set — all code paths must guard.

_word_graph = None


def set_word_graph(wg) -> None:
    """Inject the WordGraph instance at boot — called from main.py."""
    global _word_graph
    _word_graph = wg


# ── Compile-phrase pre-check ───────────────────────────────────────────────────

COMPILE_PHRASES: tuple[str, ...] = (
    "build a habit",
    "make a habit",
    "remember to always",
    "whenever ",      # "whenever X happens, you should..."
    "every time ",
    "from now on",
    "you should always",
)

# ── Threshold constants ────────────────────────────────────────────────────────

BASE_THRESHOLD = 0.50   # minimum score for any habit to fire
THRESHOLD_MIN  = 0.30
THRESHOLD_MAX  = 0.70


# ── Internal scoring ──────────────────────────────────────────────────────────

def compute_decay_factor(habit, now: datetime | None = None) -> float:
    """
    Returns a multiplier in [0, 1] representing how much of a habit's score
    to preserve based on time since last activation.

    Biological model: exponential decay with stability scaling.
    - τ_base = 30 days (half-life for activation_count=0)
    - τ scales with activation_count (experienced habits decay slower)
    - Cap at 12× = 360 days (no habit lasts forever)

    Examples:
    - 30 days unused, activation=0:  score × 0.37
    - 30 days unused, activation=10: score × 0.85 (more stable)
    - 1 year unused,  activation=0:  score × ~0.00 (effectively gone)
    - 1 year unused,  activation=20: score × 0.33 (still competitive)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Use last_accessed if available, else fall back to creation timestamp
    anchor = getattr(habit, "last_accessed", None) or getattr(habit, "timestamp", None)
    if anchor is None:
        return 1.0  # no timestamp info — don't penalize

    # Normalize to UTC-aware datetime
    if isinstance(anchor, str):
        try:
            from datetime import datetime as dt
            anchor = dt.fromisoformat(anchor.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return 1.0
    if hasattr(anchor, "tzinfo") and anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if hasattr(now, "tzinfo") and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    days_since = (now - anchor).total_seconds() / 86400.0
    if days_since <= 0:
        return 1.0

    # τ scales with activation_count: base 30d, max 360d (12×)
    activation = getattr(habit, "activation_count", 0) or 0
    tau_scale = min(1.0 + (activation * 0.5), 12.0)
    tau = 30.0 * tau_scale

    return math.exp(-days_since / tau)


def _score_habit(habit, raw_lower: str, keywords: set[str], now: datetime | None = None) -> float:
    """
    Score a single habit.  Returns 0.0 if the trigger is not in the input
    (habits without trigger present can never win).

    `now` is injectable for testability (default: current UTC time).
    """
    trigger = habit.metadata.get("trigger", "")
    if not trigger or trigger.lower() not in raw_lower:
        return 0.0  # trigger required — no trigger, no score

    score = 1.0  # base trigger score

    # keyword_bonus: overlap between parsed keywords and habit narrative words
    if keywords and habit.narrative:
        narrative_words = set(habit.narrative.lower().split())
        overlap = len(keywords & narrative_words)
        score += min(0.15, overlap * 0.05)

    # activation_bonus: more experienced habits get a gentle boost (cap at 50)
    activation = getattr(habit, "activation_count", 0) or 0
    score += min(0.15, activation * 0.003)

    # inertia_bonus: stable habits preferred (inertia is [0,1])
    inertia = getattr(habit, "inertia", 0.0) or 0.0
    score += inertia * 0.10

    # valence_bonus: positive-valence habits preferred (valence is [0,1])
    valence = getattr(habit, "valence", 0.0) or 0.0
    score += valence * 0.10

    # decay_factor: experienced habits decay slower; unused habits fade
    score *= compute_decay_factor(habit, now=now)

    return score


def _compute_threshold(milieu_state=None) -> float:
    """
    Milieu-modulated activation threshold.

    High arousal  → lower threshold (more reactive, easier to fire habits).
    Low dominance → higher threshold (less confident, escalate sooner).
    Dominance baseline is 0.3 (default competent).
    """
    t = BASE_THRESHOLD
    if milieu_state is not None:
        t -= milieu_state.arousal   * 0.08   # arousal [-1,1]
        t += (0.3 - milieu_state.dominance) * 0.06  # dominance baseline 0.3
    return max(THRESHOLD_MIN, min(THRESHOLD_MAX, t))


# ── Public API ────────────────────────────────────────────────────────────────

def select_habit(
    parsed,
    habits: list,
    milieu_state=None,
) -> "tuple[Memory | None, float]":
    """
    Score all habits in parallel; return (winner, confidence) or (None, 0.0).

    Steps:
      1. Compile-phrase pre-check → PROC_HABIT_COMPILER at 0.95.
      2. Score every habit; keep only those above milieu-modulated threshold.
      3. Winner = max score; tiebreak by activation_count.

    Never raises — habit selection must not crash the main loop.
    """
    try:
        raw_lower = parsed.raw.lower()
        keywords  = set(parsed.keywords) if parsed.keywords else set()

        # ── 1. Compile-phrase pre-check ───────────────────────────────────────
        if any(p in raw_lower for p in COMPILE_PHRASES):
            compiler = next(
                (h for h in habits if h.id == "PROC_HABIT_COMPILER"), None
            )
            if compiler:
                return (compiler, 0.95)

        # ── 2. Parallel scoring ───────────────────────────────────────────────
        threshold = _compute_threshold(milieu_state)
        now = datetime.now(timezone.utc)

        # Word graph pre-score: semantic signal over all habits at once (fast)
        _wg_scores: dict[str, float] = {}
        if _word_graph is not None:
            try:
                _wg_scores = _word_graph.score(parsed.raw, [h.id for h in habits])
            except Exception:
                pass

        scored = []
        for habit in habits:
            s = _score_habit(habit, raw_lower, keywords, now=now)
            if s > 0:  # only apply bonus when trigger matched
                s += _wg_scores.get(habit.id, 0.0) * 0.10  # word graph bonus: 0.0–0.10
            if s >= threshold:
                scored.append((s, habit))

        if not scored:
            return (None, 0.0)

        # ── 3. Winner-take-all (lateral inhibition) ───────────────────────────
        # Primary sort: descending score; tiebreak: descending activation_count
        scored.sort(
            key=lambda x: (x[0], getattr(x[1], "activation_count", 0) or 0),
            reverse=True,
        )
        winner_score, winner = scored[0]

        # Reinforce word graph: winning habit's word weights get a small boost
        if _word_graph is not None:
            try:
                _word_graph.reinforce(winner.id)
            except Exception:
                pass

        return (winner, winner_score)

    except Exception:
        return (None, 0.0)  # FAIL = Further Advance In Learning, but don't crash
