"""
confabulation_gate.py — T-watchlist-knowledge-gaps-under-load

Gate primitive instance: detects when Igor's response contains knowledge
claims that aren't grounded in retrieved context nodes.

This is distinct from action_claim_verifier (which catches false action
claims like "I ticketed it"). This gate catches knowledge confabulation:
stating facts that didn't come from retrieved memories or tools.

How it works:
    1. Extract content words from Igor's response
    2. Extract content words from the retrieved context (TWM + search results)
    3. Compute overlap — how much of the response is traceable to context
    4. If overlap is below threshold, fire the gate: push a counter-signal
       to TWM flagging the response as potentially confabulated

This is a gate evaluator function, called by the gate primitive dispatch
when a gate engram with gate_domain="confabulation" fires.

Biology: reality monitoring in metacognition. The hippocampus tags memories
with source information (did I see it, or did I imagine it?). When that
tagging fails, you get confabulation — confident false memories. This gate
is Igor's reality monitor.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)

# Minimum overlap between response content words and retrieved context.
# Below this, the response is flagged as potentially confabulated.
GROUNDING_THRESHOLD = 0.08

# Words shorter than this are excluded from overlap calculation
MIN_WORD_LEN = 3

# Minimum response length (in content words) to trigger checking.
# Very short responses rarely confabulate knowledge.
MIN_RESPONSE_WORDS = 10

# Minimum context words needed to have a meaningful baseline.
# If context is too sparse, we can't judge grounding.
MIN_CONTEXT_WORDS = 5

# Common words to exclude from overlap calculation (domain-generic)
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "has",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "way",
        "who",
        "did",
        "get",
        "let",
        "say",
        "she",
        "too",
        "use",
        "that",
        "this",
        "with",
        "have",
        "from",
        "they",
        "been",
        "will",
        "more",
        "when",
        "what",
        "some",
        "than",
        "them",
        "each",
        "which",
        "their",
        "there",
        "about",
        "would",
        "could",
        "should",
        "just",
        "like",
        "also",
        "into",
        "over",
        "such",
        "then",
        "here",
        "well",
        "only",
        "very",
        "even",
        "back",
        "after",
        "think",
        "know",
        "right",
        "look",
        "want",
        "give",
        "most",
        "make",
        "going",
        "being",
        "does",
        "don",
        "igor",
    }
)


def _tokenize(text: str) -> set[str]:
    """Extract content words from text, excluding stopwords and short words."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
    return {w for w in words if len(w) >= MIN_WORD_LEN and w not in _STOPWORDS}


def check_grounding(
    cortex: "Cortex",
    response_text: str,
    turn_id: str = "",
    thread_id: str | None = None,
) -> dict:
    """
    Check if response content is grounded in retrieved context.

    Returns:
        {
            "grounded": bool,
            "score": float,     # overlap ratio [0, 1]
            "response_words": int,
            "context_words": int,
            "flagged": bool,    # True if below threshold
            "reason": str,
        }
    """
    response_tokens = _tokenize(response_text)

    if len(response_tokens) < MIN_RESPONSE_WORDS:
        return {
            "grounded": True,
            "score": 1.0,
            "response_words": len(response_tokens),
            "context_words": 0,
            "flagged": False,
            "reason": "response too short to evaluate",
        }

    # Gather context: TWM observations + recent ring entries
    context_text = _gather_context(cortex, thread_id)
    context_tokens = _tokenize(context_text)

    if len(context_tokens) < MIN_CONTEXT_WORDS:
        return {
            "grounded": True,
            "score": 1.0,
            "response_words": len(response_tokens),
            "context_words": len(context_tokens),
            "flagged": False,
            "reason": "insufficient context for grounding check",
        }

    # Jaccard-style overlap: what fraction of response words appear in context?
    overlap = response_tokens & context_tokens
    score = len(overlap) / len(response_tokens) if response_tokens else 1.0

    flagged = score < GROUNDING_THRESHOLD

    result = {
        "grounded": not flagged,
        "score": round(score, 4),
        "response_words": len(response_tokens),
        "context_words": len(context_tokens),
        "flagged": flagged,
        "reason": (
            f"grounding score {score:.3f} below threshold {GROUNDING_THRESHOLD}"
            if flagged
            else f"grounding score {score:.3f} — adequate"
        ),
    }

    if flagged:
        # Log to ring for forensics
        try:
            cortex.write_ring(
                f"CONFAB_GATE|turn={turn_id}|score={score:.3f}"
                f"|response_words={len(response_tokens)}"
                f"|context_words={len(context_tokens)}"
                f"|overlap={len(overlap)}",
                category="confab_gate",
            )
        except Exception:
            pass

    return result


def _gather_context(cortex: "Cortex", thread_id: str | None = None) -> str:
    """Gather text from current context sources (TWM + ring)."""
    parts = []

    # TWM observations
    try:
        obs = cortex.twm_read(limit=30, thread_id=thread_id)
        for entry in obs:
            content = entry.get("content_csb", "")
            if content:
                parts.append(content)
    except Exception:
        pass

    # Recent ring entries
    try:
        ring = cortex.read_ring_memory(limit=20, thread_id=thread_id)
        for entry in ring:
            content = entry.get("content", "")
            if content:
                parts.append(content)
    except Exception:
        pass

    return " ".join(parts)


def evaluate_confabulation_gate(cortex: "Cortex", context: dict) -> tuple[bool, str]:
    """
    Gate evaluator function for the confabulation gate engram.

    Called by gate_primitive.evaluate_gate() when a gate with
    gate_domain="confabulation" fires.

    Returns (should_gate, reason).
    """
    response_text = context.get("response_text", "")
    turn_id = context.get("turn_id", "")
    thread_id = context.get("thread_id")

    if not response_text:
        return False, "no response to evaluate"

    result = check_grounding(cortex, response_text, turn_id, thread_id)

    if result["flagged"]:
        return True, result["reason"]
    return False, result["reason"]
