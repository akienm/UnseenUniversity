"""
IntentDecaySource — quiet-period push source for T-watchlist-intent-decay.

Runs surface_aged_intents from tools/intent_decay.py during quiet periods.
Follows the same shape as PRConsolidationSource (e9e3f4bd) and the existing
SleepConsolidation source: idle-gated via cortex._conversation_active_ts,
rate-limited so it doesn't fire on every tick, returns a list of TWM
observation IDs.

The watcher itself does the work (find aged goals, push them to TWM at
category='aged_intent'). This module is purely scheduling — wires the
watcher into the background-sources cadence so it runs automatically
during quiet windows. Same biological framing as the other consolidation
sources: noticing happens in the spaces between active conversation.
"""

import time
from datetime import datetime
from typing import Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

# Idle threshold — must be at least this long since the last user input
# before the scan is allowed to fire. Same value as the other quiet-period
# sources for consistency.
QUIET_THRESHOLD_SEC = 600  # 10 minutes

# Minimum gap between scans. Intent decay is cheap (one DB query + a few
# TWM pushes) but doesn't need to fire constantly. 15 minutes balances
# "stays current" with "doesn't churn".
MIN_INTERVAL_SEC = 900  # 15 minutes


class IntentDecaySource(IgorBase):
    """Quiet-period push source that runs surface_aged_intents.

    Implements the push source interface: push(cortex) → list[obs_id].
    """

    name: str = "intent_decay_source"
    TIMING_TIER: str = "slow"

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        """Run an intent-decay scan if idle conditions are met."""
        now = datetime.now()

        # Rate limit
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < MIN_INTERVAL_SEC
        ):
            return []

        # Idle gate
        if not self._is_quiet(cortex, now):
            return []

        self._last_run = now
        t0 = time.monotonic()

        try:
            from ..tools.intent_decay import surface_aged_intents

            summary = surface_aged_intents()
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Surface a single low-salience marker so the scan is observable
            # in TWM without being attentionally noisy.
            csb = (
                f"INTENT_DECAY_SCAN|complete|ms={duration_ms}"
                f"|summary={summary[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.2,
                urgency=0.1,
                ttl_seconds=600,
                category="intent_decay_scan",
                metadata={
                    "duration_ms": duration_ms,
                    "summary_excerpt": summary[:500],
                },
            )
            return [obs_id] if obs_id else []
        except Exception as e:
            log_error(
                kind="INTENT_DECAY_SOURCE",
                detail=f"Intent decay scan failed: {e}",
            )
            return []

    def _is_quiet(self, cortex, now: datetime) -> bool:
        """Same quiet check as the other quiet-period sources. Conversation_
        active_ts is None on a fresh boot — treat that as quiet so a
        just-booted Igor still scans for aged intents from prior sessions."""
        ts = getattr(cortex, "_conversation_active_ts", None)
        if ts is None:
            return True
        elapsed = (now - ts).total_seconds()
        return elapsed >= QUIET_THRESHOLD_SEC
