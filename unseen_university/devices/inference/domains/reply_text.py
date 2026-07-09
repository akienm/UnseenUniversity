"""
reply_text.py — reading a model's reply: strip the scratchpad, find the conclusion.

Shared by the escalation corpus (which verifies answers) and BaseDomain (which summarizes a
failed attempt for the next rung). One regex, one place — a second copy is a second place for
the <think>-block handling to be subtly wrong.

Why the tail, not the head
--------------------------
A reasoning model emits `<think>…scratchpad…</think>` and only then its conclusion. Slicing the
FRONT of such a reply (`text[:400]`) captures the opening of the scratchpad, cut mid-sentence —
which is exactly what the escalation handoff did until T-escalation-handoff-transmits-the-
confabulation. Measured on the live rack: handing deepseek-r1:32b the first 400 characters of
deepseek-r1:14b's scratchpad made it abandon its own correct answer and adopt the weak model's
wrong one. The conclusion lives at the END.
"""

from __future__ import annotations

import re

# A reasoning model wraps its scratchpad in <think>…</think>. An UNCLOSED block means the
# reply was truncated mid-thought: everything after it is scratchpad, not an answer.
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)
_ANSWER_TAG = re.compile(r"^\s*ANSWER\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

# Trailing chrome a model adds around an otherwise-correct answer. NB the colon is NOT here:
# stripping it would destroy a clock answer ("14:15").
_TRAILING_CHROME = " \t\n.\"'!*"


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> reasoning blocks that reasoning models emit around answers.

    A closed block is excised. An unclosed block consumes the rest of the reply — a
    truncated reasoning dump contains no answer, and reading one out of it would score a
    model on its scratchpad.
    """
    text = _THINK_BLOCK.sub("", text or "")
    return _UNCLOSED_THINK.sub("", text)


def normalize(text: str) -> str:
    """Canonical form for answer comparison: casefold, drop money/commas/bold, trim chrome."""
    t = (text or "").casefold()
    t = t.replace("$", "").replace(",", "").replace("**", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip(_TRAILING_CHROME)


def extract_answer(text: str) -> str:
    """Pull the model's final answer out of a raw reply.

    Prefers the LAST `ANSWER:` line — a model that revises ("ANSWER: 7 … wait … ANSWER: 42")
    must be read on its final claim, not its first draft. Falls back to the last non-empty
    line so a correct untagged answer is not scored as a reasoning failure.
    """
    body = strip_reasoning(text or "")
    tagged = _ANSWER_TAG.findall(body)
    if tagged:
        return tagged[-1]
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def conclusion(text: str, limit: int = 400) -> str:
    """The last `limit` characters of what the model actually CONCLUDED (scratchpad removed).

    This is what a failed attempt hands to the next rung. Taking the head instead of the tail
    hands over the beginning of a scratchpad; taking the raw text hands over the scratchpad
    itself. Either way the stronger model inherits the weaker one's reasoning and follows it.
    """
    return strip_reasoning(text or "").strip()[-limit:]
