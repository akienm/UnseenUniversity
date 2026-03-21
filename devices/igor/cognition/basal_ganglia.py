"""
Basal Ganglia — parallel habit scoring with lateral inhibition.

Replaces _find_habit() (first-match-wins) with a proper scoring model:

  score = trigger_score          # 1.0 if trigger in input, else 0.0 (required)
        + keyword_bonus          # 0.0–0.15 (narrative keyword overlap)
        + activation_bonus       # 0.0–0.15 (experienced habits slightly preferred)
        + inertia_bonus          # 0.0–0.10 (stable habits preferred over new)
        + valence_bonus          # 0.0–0.10 (positive-valence habits slightly preferred)
        + conditions_bonus       # +0.08 per matched conditions field (D201)

All habits scored in parallel; winner is max(scores). Tiebreak by activation_count.
Threshold is modulated by milieu state: high arousal → lower threshold (more reactive).

Natural-language habit-compilation phrases bypass scoring: they return
PROC_HABIT_COMPILER immediately at confidence 0.95.
"""

from __future__ import annotations
import logging

import math
import re
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
    "whenever ",  # "whenever X happens, you should..."
    "every time ",
    "from now on",
    "you should always",
)

# Notebook save-intent phrases → PROC_NOTEBOOK_SAVE at 0.93
NOTEBOOK_PHRASES: tuple[str, ...] = (
    "remember this for me",
    "save this to my notebook",
    "add this to my notebook",
    "add to my notebook",
    "save this for later",
    "keep a note of",
    "file this away",
    "notebook:",
    "add to notebook",
    "save this to the notebook",
)

# ── Threshold constants ────────────────────────────────────────────────────────

BASE_THRESHOLD = 0.50  # minimum score for any habit to fire
THRESHOLD_MIN = 0.30
THRESHOLD_MAX = 0.70


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


_COMPLEXITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def _conditions_match(conditions: dict, parsed) -> tuple[bool, int]:
    """D201: Evaluate a conditions dict against a ParsedInput.

    Returns (all_required_fields_passed, count_of_matched_fields).
    Any failing field short-circuits to (False, 0).

    Matching semantics:
      intent / tone / tags / keywords  — list, OR (any element matches)
      not_intent                        — list, negation (none must match)
      min_complexity / max_complexity   — ordinal bound
    All specified fields are AND'd: every field must pass.
    """
    if not conditions or parsed is None:
        return False, 0
    matched = 0
    actual_complexity = _COMPLEXITY_ORDER.get(
        getattr(parsed, "complexity", "medium"), 1
    )
    for key, val in conditions.items():
        if key == "intent":
            if getattr(parsed, "intent", "") in val:
                matched += 1
            else:
                return False, 0
        elif key == "not_intent":
            if getattr(parsed, "intent", "") not in val:
                matched += 1
            else:
                return False, 0
        elif key == "tone":
            if getattr(parsed, "tone", "") in val:
                matched += 1
            else:
                return False, 0
        elif key == "min_complexity":
            if actual_complexity >= _COMPLEXITY_ORDER.get(val, 0):
                matched += 1
            else:
                return False, 0
        elif key == "max_complexity":
            if actual_complexity <= _COMPLEXITY_ORDER.get(val, 2):
                matched += 1
            else:
                return False, 0
        elif key == "tags":
            actual_tags = set(getattr(parsed, "tags", []))
            if actual_tags & set(val):
                matched += 1
            else:
                return False, 0
        elif key == "keywords":
            actual_kw = {kw.lower() for kw in getattr(parsed, "keywords", [])}
            if actual_kw & {v.lower() for v in val}:
                matched += 1
            else:
                return False, 0
        # Unknown keys are ignored — forward-compat
    return matched > 0, matched


def _score_habit(
    habit,
    raw_lower: str,
    keywords: set[str],
    now: datetime | None = None,
    parsed=None,
) -> float:
    """
    Score a single habit.  Returns 0.0 if the habit's gate conditions are not met.

    Gate is determined by match_mode (D201):
      conditions_first (default): if conditions present, all must pass; trigger optional
      trigger_only:                trigger must match; conditions ignored
      both:                        trigger AND conditions must both match

    `now` is injectable for testability (default: current UTC time).
    """
    metadata = habit.metadata or {}
    trigger = metadata.get("trigger", "")
    conditions = metadata.get("conditions")
    match_mode = metadata.get("match_mode", "conditions_first")

    # ── Trigger evaluation ────────────────────────────────────────────────────
    # Trigger formats (in priority order):
    #   1. Pipe-separated phrases: "hello|hi|hey|howdy"  — word-boundary match
    #   2. Single-token exact labels: "routing_decision"  — substring match
    #   3. Legacy space-separated lists — any token of length >= 5
    trigger_ok = False
    if trigger:
        trigger_lower = trigger.lower()
        if "|" in trigger_lower:

            def _phrase_matches(phrase: str) -> bool:
                p = phrase.strip()
                if not p:
                    return False
                return bool(re.search(r"\b" + re.escape(p) + r"\b", raw_lower))

            trigger_ok = any(_phrase_matches(ph) for ph in trigger_lower.split("|"))
        elif " " in trigger_lower:
            _tokens = [t for t in trigger_lower.split() if len(t) >= 5]
            trigger_ok = bool(_tokens and any(t in raw_lower for t in _tokens))
        else:
            trigger_ok = trigger_lower in raw_lower

    # ── Conditions evaluation (D201) ──────────────────────────────────────────
    cond_ok, cond_fields = (
        _conditions_match(conditions, parsed) if conditions else (False, 0)
    )

    # ── Gate by match_mode ────────────────────────────────────────────────────
    if conditions:
        if match_mode == "trigger_only":
            if not trigger_ok:
                return 0.0
        elif match_mode == "both":
            if not (trigger_ok and cond_ok):
                return 0.0
        else:  # conditions_first (default): conditions are the primary gate
            if not cond_ok:
                return 0.0
    else:
        # No conditions — existing behavior: trigger required
        if not trigger_ok:
            return 0.0

    score = 1.0  # base score

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

    # meaning_to_me_bonus: habits tagged as personally significant get a small boost (#244)
    if metadata.get("meaning_to_me"):
        score += 0.08

    # conditions_bonus: +0.08 per matched conditions field (D201)
    score += cond_fields * 0.08

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
        t -= milieu_state.arousal * 0.08  # arousal [-1,1]
        t += (0.3 - milieu_state.dominance) * 0.06  # dominance baseline 0.3
    return max(THRESHOLD_MIN, min(THRESHOLD_MAX, t))


# ── Public API ────────────────────────────────────────────────────────────────


def select_habit(
    parsed,
    habits: list,
    milieu_state=None,
    meaning_to_me_context: bool = False,
    author: str | None = None,
) -> "tuple[Memory | None, float, list[tuple[float, Memory]]]":
    """
    Score all habits in parallel; return (winner, confidence, near_misses).

    near_misses: habits whose trigger matched but scored below the milieu-
    modulated threshold (in [THRESHOLD_MIN, threshold)).  Used by the #54
    tiebreaker path in main.py — cheap classification call before full LLM.

    meaning_to_me_context (#244): when True (caller detected a meaning_to_me TWM
        observation this turn), habits tagged with metadata.meaning_to_me=True get
        a +0.05 salience bonus so personally significant habits win tiebreaks.

    author: input author (e.g. "claude-code", "akien"). Habits with
        metadata.author_filter set are skipped unless the author matches.

    Steps:
      1. Compile-phrase pre-check → PROC_HABIT_COMPILER at 0.95.
      2. Score every habit; separate into scored (≥ threshold) and near_misses.
      3. Winner = max score; tiebreak by activation_count.

    Never raises — habit selection must not crash the main loop.
    """
    try:
        # Use core_input (thread-context stripped) for habit trigger matching.
        # parsed.raw contains the full input including prepended thread history;
        # trigger words buried in prior exchanges should not fire habits.
        _score_text = getattr(parsed, "core_input", parsed.raw)
        raw_lower = _score_text.lower()
        keywords = set(parsed.keywords) if parsed.keywords else set()

        # ── 1a. Compile-phrase pre-check ─────────────────────────────────────
        if any(p in raw_lower for p in COMPILE_PHRASES):
            compiler = next((h for h in habits if h.id == "PROC_HABIT_COMPILER"), None)
            if compiler:
                return (compiler, 0.95, [])

        # ── 1b. Notebook save-intent pre-check ───────────────────────────────
        if any(p in raw_lower for p in NOTEBOOK_PHRASES):
            saver = next((h for h in habits if h.id == "PROC_NOTEBOOK_SAVE"), None)
            if saver:
                return (saver, 0.93, [])

        # ── 2. Parallel scoring ───────────────────────────────────────────────
        threshold = _compute_threshold(milieu_state)
        now = datetime.now(timezone.utc)
        parsed_intent = getattr(parsed, "intent", "") or ""

        # G-OVN-1: intents where tool-dispatch/threshold habits should never fire.
        # Threshold habits are evaluated separately by ResourceMonitorSource + pre-submit hook.
        # Action habits with code_ref should not misfire on question vocabulary.
        _QUESTION_INTENTS = frozenset(
            {
                "factual_question",
                "knowledge_request",
                "meta_question",
                "explanation_request",
                "general",
                "conversation",
            }
        )
        # G-OVN-1d: intents where ALL response habits should fall through to LLM + winnow.
        # Canned response habits must not suppress genuine knowledge queries. (D074 expansion)
        _KNOWLEDGE_INTENTS = frozenset(
            {"factual_question", "knowledge_request", "memory_verify"}
        )

        # Word graph pre-score: semantic signal over all habits at once (fast)
        _wg_scores: dict[str, float] = {}
        if _word_graph is not None:
            try:
                _wg_scores = _word_graph.score(_score_text, [h.id for h in habits])
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/basal_ganglia.py: %s",
                    _bare_e,
                )

        scored = []
        near_misses: list[tuple[float, "Memory"]] = []
        for habit in habits:
            h_type = habit.metadata.get("habit_type", "")
            # G-OVN-1a: threshold habits evaluated by ResourceMonitorSource/pre-submit only
            if h_type == "threshold":
                continue
            # author_filter: skip habits restricted to a specific input author.
            # Prevents CC-only habits (e.g. CC_RUN_BASH) from firing on human messages.
            _af = habit.metadata.get("author_filter")
            if _af and author != _af:
                continue
            # G-OVN-1b: action-class habits (with code_ref or workflow/delegation types)
            # skip on question intents — prevent PROC_CALENDAR_CREATE, PROC_CLUSTER_SSH_CHECK
            # etc. from misfiring when a question happens to match their trigger vocabulary.
            if (
                h_type in ("action", "proactive")
                and habit.metadata.get("code_ref")
                or h_type in ("workflow", "delegation", "reactive")
            ) and parsed_intent in _QUESTION_INTENTS:
                continue
            # G-OVN-1c: response habits flagged suppress_on_factual_intent skip on
            # factual_question — prevents "I don't know that one" canned responses from
            # firing when the intent is a genuine knowledge query (#248, bug 3).
            if (
                h_type == "response"
                and habit.metadata.get("suppress_on_factual_intent")
                and parsed_intent == "factual_question"
            ):
                continue
            # G-OVN-1d: ALL response habits skip on factual_question or knowledge_request.
            # Canned responses must never suppress genuine knowledge/factual queries;
            # fall through to LLM + winnow to get a real answer. (D074 expansion, #254)
            if h_type == "response" and parsed_intent in _KNOWLEDGE_INTENTS:
                continue
            s = _score_habit(habit, raw_lower, keywords, now=now, parsed=parsed)
            if s > 0:  # only apply bonus when trigger matched
                s += _wg_scores.get(habit.id, 0.0) * 0.10  # word graph bonus: 0.0–0.10
                # #244: meaning_to_me context bonus — personally significant habits win tiebreaks
                if meaning_to_me_context and habit.metadata.get("meaning_to_me"):
                    s += 0.05
            if s >= threshold:
                scored.append((s, habit))
            elif s >= THRESHOLD_MIN:
                # Trigger matched but score below milieu-adjusted threshold.
                # Expose as near_miss for optional tiebreaker resolution (#54).
                near_misses.append((s, habit))

        near_misses.sort(key=lambda x: x[0], reverse=True)
        near_misses = near_misses[:3]  # cap — tiebreaker prompt stays small

        if not scored:
            return (None, 0.0, near_misses)

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
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/basal_ganglia.py: %s",
                    _bare_e,
                )

        return (winner, winner_score, [])

    except Exception:
        return (None, 0.0, [])  # FAIL = Further Advance In Learning, but don't crash
