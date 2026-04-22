"""
residue_scan — non-terminal emission hook for post-reply processing.

T-non-terminal-emission (D-preparse-architecture-2026-04-22).

Reply-sent ≠ turn-done. After a reflex reply fires ("Hi!") the pipeline
should keep processing the rest of the input to check whether there's
more worth responding to — the monkey-brain "Hi! ... oh, and about X..."
pattern.

This module is the pluggable hook called by _process_network_msg AFTER
the reply-pursuit completes and the parent pursuit resumes. Today it's
a stub that returns None (no second reply). T-salience-residue-scan
fills in the real logic: a graph-tree salience scan over
reply_state["input_text"] minus what the reflex reply addressed, and
if residue is high-salience, spawning a continuation-reply pursuit.

Keeping the hook and the logic in separate tickets lets T-non-terminal-
emission land the scaffolding now (input_text + reply_text threaded
through reply_state, hook invoked at the right point, parent verifiably
resumed) without requiring the scan logic to exist yet. T-salience-
residue-scan replaces this stub; main.py call site stays identical.
"""

from __future__ import annotations

from typing import Any


def scan_after_reply(
    assistant: Any,
    reply_pursuit: Any,
    reply_state: dict,
    thread_id: str | None = None,
) -> None:
    """Post-reply residue hook — stub until T-salience-residue-scan.

    Contract (for T-salience-residue-scan to honor):
      - Called AFTER reply_pursuit.evaluate_completion and resume_parent.
      - reply_state has: "delivered" (bool), "input_text" (original user
        input string), "reply_text" (what Igor emitted; may be empty if
        delivery was suppressed), optionally "addressed_span" (the
        portion of input the reflex reply consumed — None means unknown).
      - reply_pursuit is already in status=completed or abandoned.
      - Must NOT raise — log and swallow any internal error so the
        reply path never regresses on diagnostics.
      - Spawns at most one continuation-reply pursuit per call. Returns
        None regardless; side effects go through the pursuit system.
    """
    return None
