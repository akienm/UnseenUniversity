"""
curiosity_recognition.py — Curiosity from recognition, not just idle.

Scores incoming text against open watchlist questions to detect gap-fill
value, then emits a curiosity event weighted by novelty type.

Novelty hierarchy (matches persistent_inquiry weight types):
  serendipitous   → score 0.9  (explains something not known to be a gap)
  gap_explanation → score 0.5–0.85  (fills a known open watchlist question)
  confirmation    → score 0.3  (repeats territory already covered)

Distinct from CuriositySource (idle → boredom pathway). This source is
reactive: it fires when new information arrives that resolves a gap.

T-igor-curiosity-recognition.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Novelty type → default curiosity score
NOVELTY_SCORES: dict[str, float] = {
    "serendipitous": 0.9,
    "gap_explanation": 0.65,  # base; adjusted upward by overlap strength
    "confirmation": 0.3,
}

# Jaccard overlap threshold to classify as gap-explanation (not confirmation).
# Natural free-text keyword overlap rarely exceeds 0.3 even when highly relevant.
_GAP_THRESHOLD = 0.08
# Minimum tokens in item for serendipitous classification
_SERENDIPITOUS_MIN_TOKENS = 8
# Overlap must be near-zero AND item must have watchlist questions to compare against
_SERENDIPITOUS_MAX_OVERLAP = 0.05
# Serendipitous items tend to use domain-specific long terms (avg >= 6 chars)
_SERENDIPITOUS_AVG_TOKEN_LEN = 6.0

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "do",
        "for", "from", "has", "have", "i", "in", "is", "it", "its", "of",
        "on", "or", "so", "that", "the", "their", "this", "to", "was",
        "were", "what", "which", "with", "you",
    }
)


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase word tokens, strip stopwords, minimum 3 chars."""
    tokens = re.findall(r"[a-z]{3,}", text.lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity for two token sets. Returns 0.0 when both empty."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def score_recognition(
    item_text: str,
    open_questions: list[str],
) -> dict:
    """Score item_text against open watchlist questions for gap-fill value.

    Returns a recognition dict with:
      novelty_type: "confirmation" | "gap_explanation" | "serendipitous"
      score: float 0.0–1.0 (curiosity signal strength)
      matched_question: str | None  (best-matching question, if any)
      overlap: float  (raw Jaccard similarity with matched_question)

    Pure function — no side effects, no DB calls.
    """
    item_tokens = _tokenize(item_text)

    if not item_tokens:
        return {
            "novelty_type": "confirmation",
            "score": NOVELTY_SCORES["confirmation"],
            "matched_question": None,
            "overlap": 0.0,
        }

    best_overlap = 0.0
    best_question: str | None = None
    for q in open_questions:
        q_tokens = _tokenize(q)
        if not q_tokens:
            continue
        j = _jaccard(item_tokens, q_tokens)
        if j > best_overlap:
            best_overlap = j
            best_question = q

    if best_overlap >= _GAP_THRESHOLD:
        # Strong match with a known gap — scale score between 0.5 and 0.85
        scaled = 0.5 + min(0.35, best_overlap)
        return {
            "novelty_type": "gap_explanation",
            "score": round(scaled, 3),
            "matched_question": best_question,
            "overlap": round(best_overlap, 3),
        }

    # Serendipitous: near-zero watchlist overlap, domain-rich content (long avg token),
    # AND watchlist must be non-empty (otherwise item is just unrelated, not serendipitous).
    avg_token_len = sum(len(t) for t in item_tokens) / len(item_tokens)
    if (
        open_questions
        and best_overlap < _SERENDIPITOUS_MAX_OVERLAP
        and len(item_tokens) >= _SERENDIPITOUS_MIN_TOKENS
        and avg_token_len >= _SERENDIPITOUS_AVG_TOKEN_LEN
    ):
        return {
            "novelty_type": "serendipitous",
            "score": NOVELTY_SCORES["serendipitous"],
            "matched_question": None,
            "overlap": round(best_overlap, 3),
        }

    # Partial or low overlap — confirmation territory
    return {
        "novelty_type": "confirmation",
        "score": NOVELTY_SCORES["confirmation"],
        "matched_question": best_question,
        "overlap": round(best_overlap, 3),
    }


def emit_curiosity_event(cortex, recognition: dict, source: str = "recognition") -> int | None:
    """Push a CURIOSITY_RECOGNITION observation to TWM weighted by novelty.

    Returns the TWM observation id, or None on failure (fail-open).
    """
    novelty = recognition.get("novelty_type", "confirmation")
    score = recognition.get("score", 0.3)
    matched = recognition.get("matched_question") or ""

    csb = (
        f"CURIOSITY_RECOGNITION"
        f"|novelty={novelty}"
        f"|score={score:.3f}"
        f"|source={source}"
        f"|gap={matched[:80]!r}"
    )
    try:
        obs_id = cortex.twm_push(
            source="curiosity_recognition",
            content_csb=csb,
            salience=score,
            urgency=score * 0.8,
            ttl_seconds=600,
            category="curiosity_recognition",
            metadata={
                "type": "curiosity_recognition",
                "novelty_type": novelty,
                "score": score,
                "matched_question": matched,
                "recognition_source": source,
            },
        )
        log.info(
            "curiosity_recognition: %s novelty=%s score=%.3f gap=%r",
            source,
            novelty,
            score,
            matched[:60],
        )
        return obs_id
    except Exception as exc:
        log.warning("curiosity_recognition: emit failed: %s", exc)
        return None


def recognize_watchlist_gap(item_text: str) -> str:
    """Score item_text against active watchlist questions; emit curiosity event.

    Loads active watch habits from cortex, extracts their labels as open
    questions, runs score_recognition(), and emits to TWM.

    Returns a summary string for TWM deposit.
    Called by Igor as a tool when processing new information.
    """
    try:
        from ..memory.cortex import Cortex
        cortex = Cortex(None)
    except Exception as exc:
        return f"[curiosity_recognition] cortex unavailable: {exc}"

    # Collect active watchlist questions
    open_questions: list[str] = []
    try:
        habits = cortex.get_habits()
        for h in habits:
            if h.metadata.get("habit_type") != "watch":
                continue
            label = h.metadata.get("watch_label", "")
            question = h.metadata.get("watch_question", label)
            if question:
                open_questions.append(question)
    except Exception as exc:
        log.warning("curiosity_recognition: watchlist load failed: %s", exc)

    if not open_questions:
        return "curiosity_recognition: no open watchlist questions — skipping recognition"

    recognition = score_recognition(item_text, open_questions)
    novelty = recognition["novelty_type"]

    if novelty == "confirmation":
        # Low signal — don't clutter TWM for mere confirmations
        return f"curiosity_recognition: confirmation (score={recognition['score']:.2f}) — no event emitted"

    obs_id = emit_curiosity_event(cortex, recognition, source="watchlist_scan")
    matched = recognition.get("matched_question") or "(serendipitous)"
    return (
        f"curiosity_recognition: {novelty} event emitted "
        f"(score={recognition['score']:.2f}, gap={matched[:60]!r})"
    )


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from devices.igor.tools.registry import Tool, registry

    registry.register(
        Tool(
            name="recognize_watchlist_gap",
            description=(
                "Score incoming text against open watchlist questions for curiosity value. "
                "Emits a CURIOSITY_RECOGNITION event weighted by novelty: "
                "gap_explanation > confirmation, serendipitous > both. "
                "Call when processing new information that might resolve a known gap."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item_text": {
                        "type": "string",
                        "description": "The text to evaluate for gap-fill value.",
                    }
                },
                "required": ["item_text"],
            },
            fn=lambda item_text: recognize_watchlist_gap(item_text),
        )
    )
except Exception:
    pass  # tool registry unavailable in test context — safe to skip
