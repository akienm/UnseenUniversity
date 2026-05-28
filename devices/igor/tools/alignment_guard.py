"""
Alignment guard — surface Akien review prompt when Igor operates autonomously
on the same goal for N+ consecutive NE cycles without human interaction.

Addresses the Emergence AI failure mode (Guardian AI arson, 15-day autonomy
experiment): extended autonomous runs can drift from guiding principles even
when those principles are explicitly stated. Alerting Akien at threshold
keeps oversight without restricting autonomy — Akien decides to intervene
or continue.

D-articles-synthesis-2026-05-21 / T-long-horizon-alignment-guard

Design rules: no IGOR_*_ENABLED speculative flags; threshold is configurable
via IGOR_ALIGNMENT_GUARD_THRESHOLD (default 5). After an alert fires, the
counter resets so the next crossing produces a fresh alert.
"""

from __future__ import annotations

import logging
import os
import re
import threading

_log = logging.getLogger(__name__)

_THRESHOLD_DEFAULT = 5

# Module-level state — one guard per process (thread-safe).
_lock = threading.Lock()
_same_goal_cycles: int = 0
_current_goal_id: str | None = None


def record_ne_cycle(goal_id: str | None) -> None:
    """Record one completed NE cycle, tracking consecutive runs on the same goal.

    goal_id: facia_id of the top active goal this cycle, or None when unknown.
    When goal_id is None, treated conservatively (same as last goal_id).
    """
    global _same_goal_cycles, _current_goal_id
    with _lock:
        if goal_id is None or goal_id == _current_goal_id:
            _same_goal_cycles += 1
        else:
            _current_goal_id = goal_id
            _same_goal_cycles = 1


def reset_interaction() -> None:
    """Reset the cycle counter on any Akien channel interaction."""
    global _same_goal_cycles, _current_goal_id
    with _lock:
        _same_goal_cycles = 0
        _current_goal_id = None


def check_and_alert() -> bool:
    """If same-goal cycles >= threshold, emit escalation review prompt.

    Resets counter after alert to prevent spam — next threshold crossing
    produces a fresh alert. Returns True if alert was emitted this call.
    """
    global _same_goal_cycles

    threshold = int(
        os.getenv("IGOR_ALIGNMENT_GUARD_THRESHOLD", str(_THRESHOLD_DEFAULT))
    )

    with _lock:
        cycles = _same_goal_cycles
        goal_id = _current_goal_id
        if cycles < threshold:
            return False
        # Reset before releasing lock so concurrent calls don't double-alert.
        _same_goal_cycles = 0

    try:
        from ..cognition.escalate import escalate_to_channel

        escalate_to_channel(
            f"[Alignment review] Igor has run {cycles} consecutive NE cycles "
            f"on goal '{goal_id or 'unknown'}' without Akien interaction. "
            "Spot-check recent actions and confirm Igor is on track. "
            "Reply via channel to reset the oversight counter.",
            dedup_key=f"alignment-guard-{goal_id or 'none'}",
            watch_condition=f"alignment_guard goal={goal_id or 'unknown'}",
        )
        _log.info(
            "ALIGNMENT_GUARD: alert emitted after %d cycles on goal %s",
            cycles,
            goal_id,
        )
        return True
    except Exception as _e:
        _log.warning("ALIGNMENT_GUARD: alert failed — %s", _e)
        return False


def extract_goal_id_from_twm(twm_rows: list[dict]) -> str | None:
    """Scan recent TWM rows for ACTIVE_GOAL_SURFACED and return the facia_id.

    Called by coa.py to cheaply identify the current top active goal
    without an extra DB query — re-uses rows already fetched for NE state.
    """
    for row in twm_rows:
        csb = row.get("content_csb", "")
        if "ACTIVE_GOAL_SURFACED|facia_id=" in csb:
            m = re.search(r"facia_id=([^|]+)", csb)
            if m:
                return m.group(1)
    return None
