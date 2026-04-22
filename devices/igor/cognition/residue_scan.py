"""
residue_scan — non-terminal emission hook for post-reply processing.

T-non-terminal-emission (scaffolding, shipped) +
T-salience-residue-scan (this ticket — fills in the logic).

Reply-sent ≠ turn-done. After a reflex reply fires ("Hi!") the pipeline
keeps processing the rest of the input to check whether there's more
worth responding to — the monkey-brain "Hi! ... oh, and about X..."
pattern.

## Algorithm

1. Guard: if reply wasn't actually delivered, nothing to follow up on.
2. Chunk `reply_state["input_text"]` via T-input-chunker.
3. If <= 1 chunk, no residue exists → return None.
4. Classify first chunk — is it reflex-shaped (greeting, ack, very short
   fragment)? If not, the reply likely addressed the whole input and
   there's no residue to scan.
5. Residue = chunks[1:] (everything after the addressed first chunk).
6. Score residue salience via graph-tree lookups:
     - habit_list match on residue text (any registered habit trigger?)
     - content-word signal (non-stopword count)
     - question-mark presence
7. If combined score >= threshold → spawn a CONTINUATION pursuit.
   The pursuit's *articulation* (generating the second reply) is out of
   scope for this ticket — the continuation pursuit carries the residue
   text in its entry_stimulus and a later ticket (T-continuation-reply)
   will wire articulation. For now, the pursuit itself is the signal.
8. Always return None. Side effects go through the pursuit system.

## Gate

IGOR_RESIDUE_SCAN_ENABLED (default false). When disabled, this function
is effectively a no-op — it exits early without doing the scan. Matches
the observation-first rollout convention from the confidence-gated-depth
design doc.

## Logging

On every invocation (when enabled), log:
  [RESIDUE] input_chunks=N reflex=bool residue_salience=S threshold=T decision=…

So Akien can tail -f and see what the scan WOULD trigger before flipping
the gate to live continuation-reply emission.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)


# Words that are extremely common and carry little content signal —
# residue containing only these is probably noise, not a second question.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "are",
        "it",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "they",
        "we",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "and",
        "or",
        "but",
        "so",
        "if",
        "then",
        "than",
        "as",
        "also",
        "too",
        "very",
        "just",
        "only",
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "well",
        "um",
        "uh",
        "oh",
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank",
    }
)

# Short fragments that clearly indicate reflex-shaped first chunks.
_REFLEX_FRAGMENT_PATTERNS: tuple[str, ...] = (
    r"^\s*h(i|ey|ello|owdy)\b",
    r"^\s*good\s+(morning|evening|afternoon|night)\b",
    r"^\s*(thanks|thank\s+you|ty)\b",
    r"^\s*(bye|goodbye|cya|later)\b",
    r"^\s*(ok|okay|got\s+it|gotcha|sure|yep|yup|nope)\b",
)


def _default_threshold() -> float:
    try:
        return float(os.getenv("IGOR_RESIDUE_SALIENCE_THRESHOLD", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _scan_enabled() -> bool:
    return os.getenv("IGOR_RESIDUE_SCAN_ENABLED", "false").lower() == "true"


def _is_reflex_first_chunk(text: str, kind: str) -> bool:
    """Classify a chunk as reflex-shaped (greeting/ack/farewell/short-ack)."""
    if kind == "fragment":
        return True
    t = text.strip().lower()
    if len(t) <= 20:  # very short first chunks are often reflex
        for pat in _REFLEX_FRAGMENT_PATTERNS:
            if re.search(pat, t):
                return True
    return False


def _content_words(text: str) -> list[str]:
    return [
        w
        for w in re.findall(r"\b[a-z][a-z']{1,}\b", text.lower())
        if w not in _STOPWORDS
    ]


def _score_residue(
    residue_text: str,
    assistant: Any,
) -> float:
    """Combine signals into a [0,1] salience score for the residue.

    Sources (all cheap, graph-tree-only — no LLM, no cortex.search):
      - question-mark presence (strong signal of unanswered question)
      - content-word density (ratio of non-stopwords to tokens)
      - habit-trigger match (any registered habit triggered by residue?)
      - content-word count floor (≥ 3 content words = substantive)

    Weighted sum normalized to [0,1]. Conservative — a residue needs
    multiple positive signals to clear 0.5.
    """
    if not residue_text or not residue_text.strip():
        return 0.0

    score = 0.0

    # Signal 1: question mark
    if "?" in residue_text:
        score += 0.35

    # Signal 2: content-word count
    content = _content_words(residue_text)
    if len(content) >= 5:
        score += 0.30
    elif len(content) >= 3:
        score += 0.20
    elif len(content) >= 1:
        score += 0.05

    # Signal 3: content-word density
    all_tokens = re.findall(r"\b\w+\b", residue_text)
    if all_tokens:
        density = len(content) / len(all_tokens)
        if density >= 0.5:
            score += 0.15

    # Signal 4: habit-trigger match. Best-effort — if habit retrieval
    # fails or assistant doesn't expose cortex, skip rather than blow up.
    try:
        if assistant is not None and hasattr(assistant, "cortex"):
            habits = assistant.cortex.get_habits()
            lowered = residue_text.lower()
            for h in habits[:50]:  # cap to avoid O(habits) on hot path
                trig = getattr(h, "trigger", "") or ""
                if trig and trig.lower() in lowered:
                    score += 0.25
                    break
    except Exception as exc:
        log.debug("residue_scan habit match failed: %s", exc)

    # Clamp to [0,1]
    return max(0.0, min(1.0, score))


def scan_after_reply(
    assistant: Any,
    reply_pursuit: Any,
    reply_state: dict,
    thread_id: str | None = None,
) -> None:
    """Post-reply residue hook — scan unaddressed input for salient content.

    See module docstring for algorithm. Gated by IGOR_RESIDUE_SCAN_ENABLED.
    """
    try:
        if not _scan_enabled():
            return None

        if not reply_state.get("delivered"):
            return None

        input_text = reply_state.get("input_text") or ""
        if not input_text.strip():
            return None

        # Late import to avoid circulars and to defer cost until gate is on.
        from .chunker import chunk_input

        chunks = chunk_input(input_text)
        if len(chunks) <= 1:
            log.debug("[RESIDUE] single-chunk input, nothing to scan")
            return None

        first = chunks[0]
        if not _is_reflex_first_chunk(first.text, first.kind):
            log.debug(
                "[RESIDUE] first chunk not reflex-shaped (kind=%s text=%r) "
                "— reply likely addressed full input",
                first.kind,
                first.text[:40],
            )
            return None

        residue_text = " ".join(c.text for c in chunks[1:]).strip()
        if not residue_text:
            return None

        score = _score_residue(residue_text, assistant)
        threshold = _default_threshold()

        log.info(
            "[RESIDUE] input_chunks=%d reflex=True residue_salience=%.2f "
            "threshold=%.2f decision=%s residue=%r",
            len(chunks),
            score,
            threshold,
            "spawn-continuation" if score >= threshold else "drop",
            residue_text[:80],
        )

        if score < threshold:
            return None

        # Spawn continuation pursuit. The pursuit captures the residue
        # as entry_stimulus so a follow-on ticket (T-continuation-reply)
        # can articulate the second reply when it picks up the pursuit.
        try:
            from . import pursuits as pursuits_mod

            pursuits_mod.spawn(
                name="continuation_reply",
                entry_stimulus={
                    "residue_text": residue_text,
                    "salience_score": score,
                    "thread_id": thread_id,
                    "parent_reply_text": reply_state.get("reply_text"),
                    "original_input": input_text,
                },
                goal_facia=lambda s: s.get("delivered") is True,
                parent_pursuit=getattr(reply_pursuit, "id", None),
                metadata={"source": "residue_scan"},
            )
        except Exception as exc:
            log.info("residue_scan pursuit spawn failed: %s", exc)

        return None
    except Exception as exc:
        # Hard contract: must NOT raise — reply path stays unbroken.
        log.info("residue_scan failed: %s", exc)
        return None
