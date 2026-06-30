"""
gist_gate — confidence-gated short-circuit for cortex.search.

T-gist-before-retrieve.

Before this gate, cortex.search fired for every non-command turn, even when
the input was a reflex ("hi", "hello") that doesn't need episodic memory.
The thalamus/basal-ganglia pass already IS a graph-tree gist-pass (habit
match + confidence score); we just weren't using its output to decide
whether memory retrieval is even worth the latency.

Monkey-brain parallel: you recognize "friend greeting" in ~100ms and
respond reflexively. You don't pull up your full memory of this person
before saying "hi back." Full retrieval only happens when the gist-pass
is uncertain enough that memory context would help resolve it.

Usage:
    if should_skip_memory_search(parsed.intent, thalamus_habit, thalamus_confidence):
        # reflex path — build rule-based CSB, emit reply, no cortex.search
        ...
    else:
        # uncertain path — fall through to cortex.search as before
        ...

The `command` intent is unconditionally skipped (preserves existing
pre-gate behavior). Reflex intents (currently just `greeting`) are
skipped only when the gist-pass is confident, via either a selected
habit or a confidence score above threshold.
"""

from __future__ import annotations

import os
from typing import Any

# Reflex intents that don't benefit from episodic memory retrieval when
# the gist-pass is confident. `command` is handled unconditionally below
# and is not listed here.
_REFLEX_INTENTS: frozenset[str] = frozenset({"greeting"})

# Default confidence threshold for short-circuit. Tunable via env var.
_DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def _threshold() -> float:
    try:
        return float(os.getenv("IGOR_GIST_CONFIDENCE_THRESHOLD", ""))
    except ValueError:
        return _DEFAULT_CONFIDENCE_THRESHOLD
    except TypeError:
        return _DEFAULT_CONFIDENCE_THRESHOLD


def should_skip_memory_search(
    intent: str | None,
    thalamus_habit: Any,
    thalamus_confidence: float,
) -> bool:
    """Return True if cortex.search should be short-circuited for this turn.

    Gate fires when either:
      - intent == "command" (unconditional — commands never needed memory)
      - intent is in the reflex set AND the gist-pass was confident
        (habit selected OR confidence >= threshold)

    `thalamus_habit` is None-or-object; truthy iff a habit was selected.
    `thalamus_confidence` is a [0,1] score from basal_ganglia.select_habit.
    """
    if intent == "command":
        return True
    if intent in _REFLEX_INTENTS:
        if thalamus_habit is not None:
            return True
        if thalamus_confidence >= _threshold():
            return True
    return False
