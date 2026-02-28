"""
Interruptors — things that can push information into the TWM proactively.

An Interruptor is any monitor that:
  1. Checks some condition.
  2. If noteworthy, writes an alert to ring_memory (the TWM).
  3. Returns an alert string (or None if quiet).

Interruptors run at two points:
  - check(cortex) → called each interaction; writes alerts to TWM.
  - The dashboard reads active alerts from ring_memory and shows them prominently.

Adding a new interruptor:
  1. Create a class inheriting BaseInterruptor.
  2. Implement check(cortex) → str | None.
  3. Add an instance to ACTIVE_INTERRUPTORS at the bottom.
"""

from abc import ABC, abstractmethod
from datetime import datetime


class BaseInterruptor(ABC):
    """Base class for all interruptors."""

    name: str = "unnamed_interruptor"

    @abstractmethod
    def check(self, cortex=None) -> str | None:
        """
        Check the condition. If an alert is warranted:
          - Write it to cortex ring_memory (category='interruptor').
          - Return the alert string.
        If everything is fine, return None.
        cortex may be None (e.g. during early boot).
        """
        ...

    def _write_alert(self, cortex, message: str):
        """Helper: write an alert to ring_memory if cortex is available."""
        if cortex is not None:
            entry = f"[INTERRUPTOR:{self.name}] {message}"
            cortex.write_ring(entry, category="interruptor")


class BudgetInterruptor(BaseInterruptor):
    """
    Monitors Claude API spend. Fires when:
      - Budget is critical (< $2 remaining): loud warning.
      - Budget is low (< 20% remaining): softer heads-up.
      - Budget is exhausted: blocker.
    """

    name = "claude_budget"

    def check(self, cortex=None) -> str | None:
        try:
            from ..tools.budget import budget_status
            s = budget_status()
        except Exception as e:
            return None  # Budget tracker not available — don't crash

        remaining = s["remaining_usd"]
        budget    = s["budget_usd"]
        spent     = s["spent_usd"]

        if remaining <= 0:
            msg = (
                f"⛔ BUDGET EXHAUSTED! Spent ${spent:.2f} of ${budget:.2f}. "
                "Claude calls will be blocked. Akien needs to top up!"
            )
            self._write_alert(cortex, msg)
            return msg

        if s["critical"]:
            msg = (
                f"⚠️  BUDGET CRITICAL: Only ${remaining:.2f} left of ${budget:.2f}. "
                "Keep Claude calls minimal until Akien can add more funds!"
            )
            self._write_alert(cortex, msg)
            return msg

        if s["warn"]:
            msg = (
                f"⚡ Budget low: ${remaining:.2f} remaining "
                f"({100 - s['pct_used']:.0f}% of ${budget:.2f} budget left)."
            )
            self._write_alert(cortex, msg)
            return msg

        # Budget is fine — write a CLEARED entry so old alert is superseded in ring
        self._write_alert(cortex, f"✅ CLEARED: Budget OK — ${remaining:.2f} of ${budget:.2f} remaining.")
        return None  # Don't show in console — but ring entry supersedes old alert


class ContextInterruptor(BaseInterruptor):
    """
    Monitors session interaction count. Warns when context is getting long
    and expensive to send on every API call.

    Reads SESSION_START ring entry to count interactions since boot.
    Fires at WARN_AT (soft nudge) and URGENT_AT (strong push to /compress).
    Rate-limited so it doesn't fire every single interaction.
    """

    name = "context_length"
    WARN_AT = 20
    URGENT_AT = 30
    COOLDOWN_INTERACTIONS = 5  # Don't re-fire within 5 interactions

    def __init__(self):
        self._last_fired_at: int | None = None

    def check(self, cortex=None) -> str | None:
        if cortex is None:
            return None

        session_count = self._count_session_interactions(cortex)
        if session_count is None:
            return None

        # Cooldown: don't spam every interaction
        if (self._last_fired_at is not None
                and session_count - self._last_fired_at < self.COOLDOWN_INTERACTIONS):
            return None

        if session_count >= self.URGENT_AT:
            msg = (
                f"🔴 CONTEXT URGENT: {session_count} interactions this session. "
                "Context window is large and each API call is expensive. "
                "Type /compress to summarize and restart fresh."
            )
            self._write_alert(cortex, msg)
            self._last_fired_at = session_count
            return msg

        if session_count >= self.WARN_AT:
            msg = (
                f"⚡ Context notice: {session_count} interactions this session. "
                "Consider /compress to summarize context and restart fresh."
            )
            self._write_alert(cortex, msg)
            self._last_fired_at = session_count
            return msg

        return None

    def _count_session_interactions(self, cortex) -> int | None:
        """
        Count interactions since the SESSION_START ring entry.
        Ring entries are oldest-first; we walk newest-to-oldest until we hit the marker.
        Counts only Q/A summary entries (not tool traces or interruptor alerts).
        """
        try:
            entries = cortex.read_ring_memory(limit=50)  # oldest-first
            count = 0
            for entry in reversed(entries):  # walk newest → oldest
                cat = entry.get("category", "")
                if cat == "session_control" and "SESSION_START" in entry.get("content", ""):
                    return count
                # Count only substantive interaction entries
                if cat not in ("tool_trace", "interruptor", "session_control"):
                    count += 1
            return None  # No SESSION_START found
        except Exception:
            return None


# ── Active interruptors ───────────────────────────────────────────────────────
# Add new interruptors here. They'll be run automatically.

ACTIVE_INTERRUPTORS: list[BaseInterruptor] = [
    BudgetInterruptor(),
    ContextInterruptor(),
]


def run_all(cortex=None) -> list[str]:
    """
    Run all active interruptors.
    Returns a list of alert strings (empty list if everything is quiet).
    Silently swallows exceptions so a broken interruptor can't crash Igor.
    """
    alerts = []
    for interruptor in ACTIVE_INTERRUPTORS:
        try:
            result = interruptor.check(cortex)
            if result:
                alerts.append(result)
        except Exception:
            pass  # FAIL = Further Advance In Learning, but don't crash
    return alerts
