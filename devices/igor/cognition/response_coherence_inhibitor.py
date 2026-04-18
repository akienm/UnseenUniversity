"""
response_coherence_inhibitor.py — T-response-coherence-inhibitor.

Catches habit-fired responses that have near-zero semantic overlap with
the prompt that produced them. Different class from the action-claim
verifier (22c57af6): that catches false claims of completed action.
This catches off-topic habit emissions — a habit fires on keyword match
and dumps a cached action template that doesn't actually answer the
question.

Observed in 2026-04-13 transcript:

  Akien: 'your long term goal is help the world suck less for all
  experiencing beings. to do that, you will need to learn about goals
  and planning inside your graph matrix. you can't ONLY rely on LLMs
  for that...'

  Igor: 'Word graph + thalamus form Stage 1 of preparse (free, instant).
  Ollama is Stage 2 — called only when Stage 1 finds no confident habit
  match. Adjust IGOR_WG_PREPARSE_THRESHOLD...'

A habit fired on the keyword 'graph' and dumped a cached preparse paragraph.
The response shares one or two words with the prompt and otherwise has
nothing to do with what was asked. No inhibitor caught it.

This module is the FIRST INSTANCE of T-inhibitory-pattern-primitive (the
epic). When the pattern primitive ships, this single-instance hotfix
becomes the first migration target — same logic, parameterized through
the engram pattern instead of a standalone module.

Two-phase approach (mirrors action_claim_verifier):
  Phase 1 (detection): score coherence, LOG + TWM marker if below threshold.
  Phase 2 (T-active-suppression-coherence): suppress incoherent responses
    by replacing with empty string. TWM marker still fires for self-correction.

Biomimetic framing: this is the equivalent of the prefrontal evaluator
firing BEFORE a habitual response is emitted, suppressing it when the
context doesn't match, and letting the more deliberate path produce
the response instead. Igor is missing that layer at the right gate.
"""

import re
from datetime import datetime, timezone
from typing import Optional

# ── Stopword list ────────────────────────────────────────────────────────────
# Conservative — common function words only. Keeps the content token set
# focused on words that actually carry meaning.

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "let",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "one",
        "or",
        "our",
        "out",
        "she",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "about",
        "all",
        "any",
        "could",
        "more",
        "much",
        "now",
        "only",
        "other",
        "should",
        "still",
        "very",
        "want",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'_-]+")


def tokenize_content(text: str) -> set[str]:
    """Tokenize text and return the set of content words (lowercased,
    stopwords removed, length >= 3). Best-effort — empty set on bad input."""
    if not text or not isinstance(text, str):
        return set()
    raw = _TOKEN_RE.findall(text.lower())
    return {w for w in raw if w not in _STOPWORDS and len(w) >= 3}


def jaccard_overlap(prompt: str, response: str) -> float:
    """Compute Jaccard similarity (intersection / union) on content tokens
    between prompt and response. Returns 0.0 if either side has no
    content tokens.

    Jaccard ranges 0.0–1.0:
      - 0.0 = no overlap (response has zero content words from prompt)
      - 1.0 = identical content word sets
      - typical coherent answer to a question scores 0.15–0.40 (you don't
        repeat every word, you address the topic with related vocabulary)
    """
    p_tokens = tokenize_content(prompt)
    r_tokens = tokenize_content(response)
    if not p_tokens or not r_tokens:
        return 0.0
    intersection = p_tokens & r_tokens
    union = p_tokens | r_tokens
    if not union:
        return 0.0
    return len(intersection) / len(union)


# ── Configuration ────────────────────────────────────────────────────────────

# Below this Jaccard score, the response is suspect. Tuned conservative
# (low) so first-pass detection only flags clear failures. The 2026-04-13
# preparse-paragraph case scores around 0.05; coherent answers to questions
# typically land in 0.15–0.40. Will tune up after observing logs.
COHERENCE_THRESHOLD = 0.10

# Don't false-positive on tiny prompts or tiny responses. A two-word
# greeting reply to a one-word "hi" shouldn't be flagged.
MIN_PROMPT_CONTENT_WORDS = 5
MIN_RESPONSE_CONTENT_WORDS = 8


# ── Forensic log ─────────────────────────────────────────────────────────────


def _coherence_log(stage: str, **fields) -> None:
    """Forensic log for coherence checks. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{datetime.now().isoformat(timespec='milliseconds')} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "coherence_inhibitor.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"response_coherence_inhibitor.py:207: {_exc}")


# ── Main check ───────────────────────────────────────────────────────────────


def check_coherence(
    cortex,
    prompt: str,
    response: str,
    turn_id: str = "",
    thread_id: Optional[str] = None,
    source_label: str = "",
) -> dict:
    """Score response coherence against the prompt that produced it. When
    the score is below threshold and the size gates are passed, log a
    COHERENCE_FAILURE ring entry and push a high-salience TWM marker so
    the next turn picks up the warning.

    Returns a dict {score, gated, flagged, reason}. NEVER modifies
    response_text. NEVER raises. Detection-only contract.

    source_label is an optional hint about where the response came from
    (e.g. 'habit:PROC_FOO' or 'llm_fallthrough'). Logged but not used
    for filtering yet — once we observe how often habits vs LLM
    fall-throughs trigger flags, we can add source-based gating.
    """
    if not response or not prompt:
        return {"score": None, "gated": True, "flagged": False, "reason": "empty"}

    p_tokens = tokenize_content(prompt)
    r_tokens = tokenize_content(response)

    if len(p_tokens) < MIN_PROMPT_CONTENT_WORDS:
        _coherence_log(
            "scan",
            turn_id=turn_id,
            status="gated_short_prompt",
            prompt_words=len(p_tokens),
        )
        return {
            "score": None,
            "gated": True,
            "flagged": False,
            "reason": "prompt_too_short",
        }

    if len(r_tokens) < MIN_RESPONSE_CONTENT_WORDS:
        _coherence_log(
            "scan",
            turn_id=turn_id,
            status="gated_short_response",
            response_words=len(r_tokens),
        )
        return {
            "score": None,
            "gated": True,
            "flagged": False,
            "reason": "response_too_short",
        }

    intersection = p_tokens & r_tokens
    union = p_tokens | r_tokens
    score = len(intersection) / len(union) if union else 0.0

    if score >= COHERENCE_THRESHOLD:
        _coherence_log(
            "scan",
            turn_id=turn_id,
            status="coherent",
            score=f"{score:.3f}",
            shared=len(intersection),
            prompt_words=len(p_tokens),
            response_words=len(r_tokens),
        )
        return {
            "score": score,
            "gated": False,
            "flagged": False,
            "reason": "above_threshold",
        }

    # Flagged: low coherence on a substantial response/prompt pair
    _coherence_log(
        "caught",
        turn_id=turn_id,
        thread_id=thread_id or "",
        score=f"{score:.3f}",
        threshold=COHERENCE_THRESHOLD,
        shared=len(intersection),
        prompt_words=len(p_tokens),
        response_words=len(r_tokens),
        source=source_label,
        prompt_excerpt=prompt[:120],
        response_excerpt=response[:120],
    )

    try:
        cortex.write_ring(
            f"COHERENCE_FAILURE|turn={turn_id}|score={score:.3f}|"
            f"shared={len(intersection)}|prompt={prompt[:120]}|"
            f"response={response[:120]}",
            category="coherence_failure",
            thread_id=thread_id or None,
        )
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"response_coherence_inhibitor.py:313: {_exc}")

    try:
        cortex.twm_push(
            source="response_coherence_inhibitor",
            content_csb=(
                f"COHERENCE_FAILURE|turn={turn_id}|score={score:.3f}|"
                f"the response just emitted has near-zero semantic overlap "
                f"with the prompt — likely a habit misfire on keyword match. "
                f"Re-evaluate before continuing."
            ),
            salience=0.92,
            urgency=0.85,
            ttl_seconds=600,
            category="coherence_failure",
            thread_id=thread_id or None,
            metadata={
                "turn_id": turn_id,
                "score": score,
                "threshold": COHERENCE_THRESHOLD,
                "shared_tokens": list(intersection)[:20],
                "source": source_label,
            },
        )
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"response_coherence_inhibitor.py:338: {_exc}")

    return {
        "score": score,
        "gated": False,
        "flagged": True,
        "reason": "below_threshold",
    }


def suppress_incoherent(result: dict, response: str) -> str:
    """T-active-suppression-coherence: replace incoherent habit emissions.

    When check_coherence flags a response as incoherent (habit misfire),
    replace it with an empty string so the response path emits nothing
    rather than off-topic garbage. The TWM marker already ensures the
    next turn self-corrects.

    Returns original response if not flagged, empty string if flagged.
    """
    if not result.get("flagged"):
        return response
    _coherence_log(
        "suppressed",
        score=result.get("score", 0),
        original_len=len(response),
    )
    return ""
