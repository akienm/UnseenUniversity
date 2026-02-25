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

        return None  # All good, stay quiet


# ── Active interruptors ───────────────────────────────────────────────────────
# Add new interruptors here. They'll be run automatically.

ACTIVE_INTERRUPTORS: list[BaseInterruptor] = [
    BudgetInterruptor(),
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
