import logging

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

from ..igor_base import get_logger
from ..igor_base import IgorBase
from ..paths import paths


class BaseInterruptor(ABC, IgorBase):
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
    Monitors OpenRouter spend. Fires when:
      - Balance is critical (< $2 remaining): loud warning.
      - Balance is low (< 20% remaining): softer heads-up.
      - Balance is exhausted: blocker.

    Writes to ring only on state changes (alert→OK or OK→alert) to avoid
    flooding the ring with repeated identical entries.
    """

    name = "openrouter_budget"

    def __init__(self):
        self._was_alerting: bool = False

    def check(self, cortex=None) -> str | None:
        try:
            from unseen_university.devices.igor.tools.resource_manager import budget_status

            s = budget_status()
        except Exception:
            return None  # Budget tracker not available — don't crash

        remaining = s["remaining_usd"]
        total = s.get("purchased_usd") or s.get("spending_cap", 0)
        spent = s.get("used_usd") or s.get("local_spent", 0)
        src = s.get("source", "local_tracking")

        if remaining <= 0:
            msg = (
                f"⛔ BALANCE EXHAUSTED ({src})! Used ${spent:.2f} of ${total:.2f}. "
                "OpenRouter calls blocked. Let Akien know!"
            )
            self._was_alerting = True
            self._write_alert(cortex, msg)
            return msg

        if s["critical"]:
            msg = (
                f"⚠️  BALANCE CRITICAL ({src}): Only ${remaining:.2f} left of ${total:.2f}. "
                "Keep cloud inference calls minimal!"
            )
            self._was_alerting = True
            self._write_alert(cortex, msg)
            return msg

        if s["warn"]:
            msg = (
                f"⚡ Balance low ({src}): ${remaining:.2f} remaining "
                f"({100 - s['pct_used']:.0f}% of ${total:.2f} left)."
            )
            self._was_alerting = True
            self._write_alert(cortex, msg)
            return msg

        # Balance is fine.
        if self._was_alerting:
            # Write CLEARED once to supersede prior alert in the ring, then go silent.
            self._write_alert(
                cortex, f"✅ CLEARED: Balance OK — ${remaining:.2f} remaining ({src})."
            )
            self._was_alerting = False
        return None


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
        if (
            self._last_fired_at is not None
            and session_count - self._last_fired_at < self.COOLDOWN_INTERACTIONS
        ):
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
                if cat == "session_control" and "SESSION_START" in entry.get(
                    "content", ""
                ):
                    return count
                # Count only substantive interaction entries
                if cat not in ("tool_trace", "interruptor", "session_control"):
                    count += 1
            return None  # No SESSION_START found
        except Exception:
            return None


class MilieuInterruptor(BaseInterruptor):
    """
    Fires when Igor's ambient emotional state reaches sustained extremes.
    Writes to ring and returns alert string — NE and human can act on it.

    Thresholds are deliberately high: milieu drifts slowly, so if arousal
    exceeds 0.7 or valence stays below -0.5 it's a genuine signal, not noise.
    """

    name = "milieu"
    AROUSAL_HIGH = 0.70
    VALENCE_LOW = -0.50
    COOLDOWN_TICKS = 10  # don't re-fire within 10 milieu ticks (~10 interactions)

    def __init__(self):
        self._last_fired_tick: int | None = None

    def check(self, cortex=None) -> str | None:
        try:
            from . import milieu as milieu_mod

            m = milieu_mod.get()
            if m is None:
                return None

            state = m.get_state()

            # Cooldown — don't spam
            if (
                self._last_fired_tick is not None
                and state.tick - self._last_fired_tick < self.COOLDOWN_TICKS
            ):
                return None

            if state.arousal > self.AROUSAL_HIGH:
                msg = (
                    f"High arousal ({state.arousal:.2f}) — sustained activation state. "
                    "Consider pacing: fewer tool calls, shorter responses."
                )
                self._write_alert(cortex, msg)
                self._last_fired_tick = state.tick
                return msg

            if state.valence < self.VALENCE_LOW:
                msg = (
                    f"Sustained negative valence ({state.valence:.2f}) — mood trending low. "
                    "May indicate repeated friction or unresolved failures."
                )
                self._write_alert(cortex, msg)
                self._last_fired_tick = state.tick
                return msg

        except Exception as _bare_e:
            get_logger(__name__).warning(
                "bare except in devices/igor/cognition/interruptors.py: %s", _bare_e
            )

        return None


class DiskInterruptor(BaseInterruptor):
    """
    Monitors free disk space for Igor's runtime paths.
    Fires when free space drops below IGOR_DISK_WARN_GB (default 1GB)
    or IGOR_DISK_CRITICAL_GB (default 0.2GB).
    Rate-limited: re-checks every 50 interactions to avoid shutil overhead every turn.
    """

    name = "disk_space"
    COOLDOWN_INTERACTIONS = 50

    def __init__(self):
        self._last_fired_at: int = 0
        self._interaction_count: int = 0
        self._was_alerting: bool = False

    def check(self, cortex=None) -> str | None:
        import os
        import shutil
        from pathlib import Path

        self._interaction_count += 1
        if self._interaction_count - self._last_fired_at < self.COOLDOWN_INTERACTIONS:
            return None

        warn_gb = float(os.getenv("IGOR_DISK_WARN_GB", "1.0"))
        crit_gb = float(os.getenv("IGOR_DISK_CRITICAL_GB", "0.2"))

        try:
            usage = shutil.disk_usage(str(paths().runtime))
            free_gb = usage.free / (1024**3)
        except Exception:
            return None

        self._last_fired_at = self._interaction_count

        if free_gb < crit_gb:
            msg = (
                f"⛔ DISK CRITICAL: Only {free_gb:.2f} GB free on ~/.unseen_university partition. "
                "Stop large writes immediately. Skipping backups. Alert Akien!"
            )
            self._was_alerting = True
            self._write_alert(cortex, msg)
            return msg

        if free_gb < warn_gb:
            msg = (
                f"⚠️  DISK WARN: {free_gb:.2f} GB free on ~/.unseen_university partition. "
                "Consider pruning logs or old cache files."
            )
            self._was_alerting = True
            self._write_alert(cortex, msg)
            return msg

        if self._was_alerting:
            self._write_alert(
                cortex, f"✅ CLEARED: Disk space OK — {free_gb:.2f} GB free."
            )
            self._was_alerting = False

        return None


# ── Active interruptors ───────────────────────────────────────────────────────
# Add new interruptors here. They'll be run automatically.

ACTIVE_INTERRUPTORS: list[BaseInterruptor] = [
    BudgetInterruptor(),
    ContextInterruptor(),
    MilieuInterruptor(),
    DiskInterruptor(),
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
        except Exception as _bare_e:
            get_logger(__name__).warning(
                "bare except in devices/igor/cognition/interruptors.py: %s", _bare_e
            )
    return alerts
