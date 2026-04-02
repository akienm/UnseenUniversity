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
import os

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .eval_gate import eval_gate as _eval_gate

if TYPE_CHECKING:
    from ..models import Memory
    from .milieu import MilieuState


# ── Word graph integration ─────────────────────────────────────────────────────
# Injected at boot by main.py. None until set — all code paths must guard.

_word_graph = None
_cortex = None


def set_word_graph(wg) -> None:
    """Inject the WordGraph instance at boot — called from main.py."""
    global _word_graph
    _word_graph = wg


def set_cortex(cx) -> None:
    """Inject the Cortex instance at boot — called from main.py."""
    global _cortex
    _cortex = cx


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

# Management command phrases → direct habit dispatch at 0.97, bypassing BG scoring.
# When these match, the habit's code_ref executes the tool without any LLM call.
# Keeps management commands responsive even under heavy inference load.
MANAGEMENT_PHRASES: dict[str, str] = {
    "update swarm": "PROC_SWARM_UPDATE",
    "swarm update": "PROC_SWARM_UPDATE",
    "pull and restart": "PROC_SWARM_UPDATE",
    "cluster status": "PROC_CLUSTER_STATUS",
    "check cluster": "PROC_CLUSTER_STATUS",
    "cluster load": "PROC_CLUSTER_STATUS",
    # D275: task→goal adoption — direct dispatch so goal_adopt always wins over
    # PROC_CHECK_IGOR_QUEUE and other cognitive habits that score on 'ticket' tokens.
    "work ticket": "20260324123708036175",
    "work the ticket": "20260324123708036175",
    "implement ticket": "20260324123708036175",
    "fix ticket": "20260324123708036175",
    # D302a: self-management direct dispatch
    "restart self": "PROC_RESTART_SELF",
    "restart_self": "PROC_RESTART_SELF",
    "flush habit cache": "PROC_FLUSH_HABIT_CACHE",
    "flush_habit_cache": "PROC_FLUSH_HABIT_CACHE",
    # D302: coding cascade direct dispatch — bypass BG so these always fire reliably
    # even when tier.2 LLM generates intent text instead of tool call JSON.
    "adopt top ticket": "PROC_QUEUE_DRAIN",
    "adopt ticket": "PROC_QUEUE_DRAIN",
    "queue drain": "PROC_QUEUE_DRAIN",
    "goal continuation": "PROC_GOAL_CONTINUATION",
    "continue goal": "PROC_GOAL_CONTINUATION",
    "advance goal": "PROC_GOAL_CONTINUATION",
    "coding sprint": "PROC_CODING_SPRINT",
    "run coding sprint": "PROC_CODING_SPRINT",
    "close goal": "PROC_CLOSE_GOAL",
    "close the goal": "PROC_CLOSE_GOAL",
    "mark goal done": "PROC_CLOSE_GOAL",
    "goal done": "PROC_CLOSE_GOAL",
    "read active goal": "PROC_TWM_READ_GOAL",
    "what is my goal": "PROC_TWM_READ_GOAL",
    "check active goal": "PROC_TWM_READ_GOAL",
    "what goal": "PROC_TWM_READ_GOAL",
}

# ── Threshold constants ────────────────────────────────────────────────────────

BASE_THRESHOLD = 0.50  # minimum score for any habit to fire
THRESHOLD_MIN = 0.30
THRESHOLD_MAX = 0.70

# ── Refractory period constants (T-refractory-period) ─────────────────────────

_REFRACTORY_TTL_SEC = float(os.getenv("IGOR_REFRACTORY_TTL_SEC", "600"))  # 10 min
_REFRACTORY_FACTOR = float(os.getenv("IGOR_REFRACTORY_FACTOR", "0.1"))  # 10% score

# T-refractory-period: in-process refractory state — cleared on restart
_refractory_map: dict[str, float] = {}  # habit_id → expiry_timestamp (UTC epoch)


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

    actual_complexity = _COMPLEXITY_ORDER.get(
        getattr(parsed, "complexity", "medium"), 1
    )
    # Namespace maps each condition key to its parsed value for eval_gate.
    # not_intent reuses the "intent" field with the not_member_of op.
    ns: dict = {
        "intent": getattr(parsed, "intent", ""),
        "not_intent": getattr(parsed, "intent", ""),
        "tone": getattr(parsed, "tone", ""),
        "min_complexity": actual_complexity,
        "max_complexity": actual_complexity,
        "tags": set(getattr(parsed, "tags", [])),
        "keywords": {kw.lower() for kw in getattr(parsed, "keywords", [])},
    }
    # Op to use per condition key
    _OPS = {
        "intent": "member_of",
        "not_intent": "not_member_of",
        "tone": "member_of",
        "min_complexity": ">=",
        "max_complexity": "<=",
        "tags": "intersects",
        "keywords": "intersects",
    }

    matched = 0
    for key, val in conditions.items():
        op = _OPS.get(key)
        if op is None:
            continue  # unknown keys — forward-compat
        # Normalise rhs for ops that need it
        if key == "keywords":
            val = {v.lower() for v in val}
        elif key == "min_complexity":
            val = _COMPLEXITY_ORDER.get(val, 0)
        elif key == "max_complexity":
            val = _COMPLEXITY_ORDER.get(val, 2)
        if _eval_gate(key, op, val, ns):
            matched += 1
        else:
            return False, 0
    return matched > 0, matched


def _apply_intent_gate(
    habit,
    parsed_intent: str,
    author: str | None = None,
) -> bool:
    """
    D201 intent gate: determine if a habit should be scored based on parsed intent and metadata.

    Returns True if the habit passes all gates (should be scored).
    Returns False if the habit should be skipped due to intent or author filter.

    Gates (in order):
      1. Threshold habits skip (evaluated separately by ResourceMonitorSource)
      2. Author filter: skip if habit restricts to a different author
      3. Action-class skip on question intents: prevent tools from misfiring
      4. Response habits skip on factual intent (suppress_on_factual_intent flag)
      5. Response habits skip on knowledge intents (all responses fall through on factual/knowledge)
    """
    metadata = habit.metadata or {}
    h_type = metadata.get("habit_type", "")

    # G-OVN-1a: threshold habits evaluated by ResourceMonitorSource/pre-submit only
    if h_type == "threshold":
        return False

    # author_filter: skip if restricted to a different input author
    _af = metadata.get("author_filter")
    if _af:
        _af_list = _af if isinstance(_af, list) else [_af]
        if author not in _af_list:
            return False

    # Intent-based gates
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
    _KNOWLEDGE_INTENTS = frozenset(
        {"factual_question", "knowledge_request", "memory_verify"}
    )

    # G-OVN-1b: action-class habits skip on question intents
    if (
        h_type in ("action", "proactive", "workflow", "delegation", "reactive")
        and parsed_intent in _QUESTION_INTENTS
    ):
        return False

    # G-OVN-1c: response habits with suppress_on_factual_intent flag
    if (
        h_type == "response"
        and metadata.get("suppress_on_factual_intent")
        and parsed_intent == "factual_question"
    ):
        return False

    # G-OVN-1d: ALL response habits skip on factual/knowledge intents
    if h_type == "response" and parsed_intent in _KNOWLEDGE_INTENTS:
        return False

    return True


def _apply_specificity_bonus(
    habit,
    parsed=None,
    _wg_scores: dict | None = None,
    meaning_to_me_context: bool = False,
) -> float:
    """
    Calculate total specificity bonus for a habit.

    Specificity bonus includes:
      1. conditions_bonus: +0.08 per matched conditions field (D201)
      2. word_graph_bonus: 0.0–0.10 based on semantic alignment
      3. meaning_to_me context bonus: +0.05 if habit is personally significant

    Returns the total bonus amount to add to the base score.
    """
    if _wg_scores is None:
        _wg_scores = {}

    bonus = 0.0

    # conditions_bonus: +0.08 per matched conditions field (D201)
    metadata = habit.metadata or {}
    conditions = metadata.get("conditions")
    if conditions and parsed:
        cond_ok, cond_fields = _conditions_match(conditions, parsed)
        bonus += cond_fields * 0.08

    # word_graph_bonus: semantic alignment with input
    bonus += _wg_scores.get(habit.id, 0.0) * 0.10

    # meaning_to_me context bonus: personally significant habits win tiebreaks (#244)
    if meaning_to_me_context and metadata.get("meaning_to_me"):
        bonus += 0.05

    return bonus


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

    # decay_factor: experienced habits decay slower; unused habits fade
    score *= compute_decay_factor(habit, now=now)

    # T-refractory-period: suppress recently-fired habits
    if habit.id in _refractory_map:
        expiry = _refractory_map[habit.id]
        ts = (
            now.timestamp()
            if now is not None
            else datetime.now(timezone.utc).timestamp()
        )
        if ts < expiry:
            score *= _REFRACTORY_FACTOR
        else:
            del _refractory_map[habit.id]  # expired — clean up

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


# ── Inhibition propagation ────────────────────────────────────────────────────


def _inhibit_neighbors(winner_id: str, near_miss_ids: list[str]) -> None:
    """
    After BG winner selected: write inhibition edges from winner to any
    near-miss habits that share an existing interpretive edge with the winner.

    Uses direction="inhibition" + layer="bg_inhibition" — already respected by
    graph traversal (cortex._find_related skips inhibited subtrees).

    Graceful no-op if _cortex is None or winner has no edges yet.
    Logs BG_INHIBITION to ring(ne_diagnostic) when edges are written.
    """
    if _cortex is None or not near_miss_ids:
        return
    try:
        edges = _cortex.get_interpretive_edges(winner_id)
        neighbor_ids = {e["to_id"] for e in edges}
        targets = [nid for nid in near_miss_ids if nid in neighbor_ids]
        if not targets:
            return
        for target_id in targets:
            _cortex.add_interpretive_edge(
                from_id=winner_id,
                to_id=target_id,
                direction="inhibition",
                weight=0.3,
                layer="bg_inhibition",
            )
        _cortex.write_ring(
            f"BG_INHIBITION|winner={winner_id}|targets={len(targets)}",
            category="ne_diagnostic",
        )
    except Exception as _e:
        logging.getLogger(__name__).debug("_inhibit_neighbors: %s", _e)


# ── Public API ────────────────────────────────────────────────────────────────


def select_habit(
    parsed,
    habits: list,
    milieu_state=None,
    meaning_to_me_context: bool = False,
    author: str | None = None,
    active_goal_keywords: "set[str] | None" = None,
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

    def _emit_bg(data: dict) -> None:
        """Emit BG scoring summary to TurnContext (turn_trace). Fire-and-forget."""
        try:
            from .forensic_logger import turn_ctx_update as _tcu

            _tcu("bg_scoring", data)
        except Exception as e:
            from .forensic_logger import log_error

            log_error(kind="TOOL_FAIL", detail=f"emit_bg failed: {e}")  # non-fatal

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
                _emit_bg(
                    {
                        "pre_check": "compile_phrase",
                        "winner": "PROC_HABIT_COMPILER",
                        "winner_score": 0.95,
                        "top": [],
                        "near_misses": 0,
                        "rationale": "compile_phrase_match",
                    }
                )
                return (compiler, 0.95, [])

        # ── 1b. Notebook save-intent pre-check ───────────────────────────────
        if any(p in raw_lower for p in NOTEBOOK_PHRASES):
            saver = next((h for h in habits if h.id == "PROC_NOTEBOOK_SAVE"), None)
            if saver:
                _emit_bg(
                    {
                        "pre_check": "notebook_phrase",
                        "winner": "PROC_NOTEBOOK_SAVE",
                        "winner_score": 0.93,
                        "top": [],
                        "near_misses": 0,
                        "rationale": "notebook_phrase_match",
                    }
                )
                return (saver, 0.93, [])

        # ── 1c. Management command pre-check ─────────────────────────────────
        # Direct dispatch for operational commands — bypasses BG scoring entirely.
        # Ensures management habits fire even under heavy inference load.
        for phrase, habit_id in MANAGEMENT_PHRASES.items():
            if phrase in raw_lower:
                mgmt_habit = next((h for h in habits if h.id == habit_id), None)
                if mgmt_habit:
                    _emit_bg(
                        {
                            "pre_check": "management_phrase",
                            "winner": habit_id,
                            "winner_score": 0.97,
                            "top": [],
                            "near_misses": 0,
                            "rationale": f"management_phrase_match:{phrase}",
                        }
                    )
                    return (mgmt_habit, 0.97, [])

        # ── 2. Parallel scoring ───────────────────────────────────────────────
        threshold = _compute_threshold(milieu_state)
        now = datetime.now(timezone.utc)
        parsed_intent = getattr(parsed, "intent", "") or ""

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
            # Apply intent gate: skip habits that shouldn't be scored based on intent/author
            if not _apply_intent_gate(habit, parsed_intent, author=author):
                continue

            s = _score_habit(habit, raw_lower, keywords, now=now, parsed=parsed)
            if s > 0:  # only apply bonus when trigger matched
                s += _apply_specificity_bonus(
                    habit,
                    parsed=parsed,
                    _wg_scores=_wg_scores,
                    meaning_to_me_context=meaning_to_me_context,
                )
            if s >= threshold:
                scored.append((s, habit))
            elif s >= THRESHOLD_MIN:
                # Trigger matched but score below milieu-adjusted threshold.
                # Expose as near_miss for optional tiebreaker resolution (#54).
                near_misses.append((s, habit))

        near_misses.sort(key=lambda x: x[0], reverse=True)
        near_misses = near_misses[:3]  # cap — tiebreaker prompt stays small

        if not scored:
            _emit_bg(
                {
                    "threshold": round(threshold, 4),
                    "winner": None,
                    "winner_score": 0.0,
                    "top": [],
                    "near_misses": len(near_misses),
                    "near_miss_ids": [h.id for _, h in near_misses],
                    "rationale": "no_candidates_above_threshold",
                }
            )
            return (None, 0.0, near_misses)

        # ── 2b. Goal-context boost (D275 lateral inhibition) ─────────────────
        # When an active GOAL is in TWM, habits whose trigger overlaps with the
        # goal's keywords get +0.20 boost. Unrelated action habits get -0.15
        # penalty. This makes goal-relevant habits win over noise action nodes.
        if active_goal_keywords and scored:
            boosted = []
            for s, habit in scored:
                trigger = habit.metadata.get("trigger", "").lower()
                trigger_words = set(trigger.replace("|", " ").split())
                if active_goal_keywords & trigger_words:
                    s = min(1.0, s + 0.20)  # goal-relevant: boost
                elif habit.metadata.get("habit_type") == "action":
                    s = max(0.0, s - 0.15)  # unrelated action: dampen
                boosted.append((s, habit))
            # Re-filter: damped action habits may fall below threshold
            scored = [(s, h) for s, h in boosted if s >= threshold]
            near_misses_extra = [
                (s, h) for s, h in boosted if THRESHOLD_MIN <= s < threshold
            ]
            near_misses = (near_misses + near_misses_extra)[:3]

        # ── 3. Winner-take-all (lateral inhibition) ───────────────────────────
        # Primary sort: descending score; tiebreak: descending activation_count
        scored.sort(
            key=lambda x: (x[0], getattr(x[1], "activation_count", 0) or 0),
            reverse=True,
        )
        winner_score, winner = scored[0]

        # Reinforce word graph: winning habit's word weights get a small boost.
        # #338: scale by surprise (prediction flatness) — novel input → bigger reward.
        if _word_graph is not None:
            try:
                _flatness = _word_graph.gradient_flatness(_score_text)
                _surprise = _word_graph.surprise_scale(_flatness)
                _boost = 0.1 * _surprise
                _word_graph.reinforce(winner.id, boost=_boost)
                if _surprise > 1.5:
                    logging.getLogger(__name__).debug(
                        "surprise_reward: habit=%s flatness=%.2f scale=%.2f boost=%.3f",
                        winner.id,
                        _flatness,
                        _surprise,
                        _boost,
                    )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/basal_ganglia.py: %s",
                    _bare_e,
                )

        # Propagate inhibition: winner suppresses graph-connected near-misses.
        _inhibit_neighbors(winner.id, [h.id for _, h in scored[1:5]])

        _emit_bg(
            {
                "threshold": round(threshold, 4),
                "winner": winner.id,
                "winner_score": round(winner_score, 4),
                "top": [
                    {
                        "id": h.id,
                        "score": round(s, 4),
                        "type": h.metadata.get("habit_type", ""),
                    }
                    for s, h in scored[:5]
                ],
                "near_misses": len(near_misses),
                "rationale": "max_score_wins",
            }
        )
        # T-refractory-period: mark winner as recently fired
        _refractory_map[winner.id] = (
            datetime.now(timezone.utc).timestamp() + _REFRACTORY_TTL_SEC
        )
        return (winner, winner_score, [])

    except Exception:
        return (None, 0.0, [])  # FAIL = Further Advance In Learning, but don't crash
