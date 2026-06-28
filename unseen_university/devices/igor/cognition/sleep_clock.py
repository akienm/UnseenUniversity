"""
sleep_clock.py — T-sleep-triggered-by-clock (#467)

Clock-gated sleep safety net. Humans sleep from tiredness (idle
detection, already built) OR from habit (bedtime arrives → sleep).
This is the clock habit.

Rule: if it's nighttime (22:00-07:00) AND Igor hasn't entered sleep
recently → trigger sleep maintenance. Not "always sleep at 22:00" but
"if it's bedtime and you haven't slept in a while, sleep."

Sleep maintenance runs:
  - Deep consolidation pass (NarrativeEngine._deep_consolidation_pass)
  - SleepConsolidation binding discovery
  - TWM marker so other sources (self-training, pr_consolidation) can
    gate on sleep state

Inertia: LOW (new push source, doesn't touch brainstem)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error
from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)

SLEEP_WINDOW_START = 22
SLEEP_WINDOW_END = 7
MIN_AWAKE_HOURS = 4.0
MIN_SLEEP_INTERVAL_SEC = 3600.0


def _in_sleep_window(hour: int) -> bool:
    """True if the given hour falls in the nighttime window."""
    if SLEEP_WINDOW_START <= SLEEP_WINDOW_END:
        return SLEEP_WINDOW_START <= hour < SLEEP_WINDOW_END
    return hour >= SLEEP_WINDOW_START or hour < SLEEP_WINDOW_END


class SleepClockSource(IgorBase):
    """Push source that triggers sleep maintenance on a clock schedule.

    Checks once per slow-tier cycle (5 min). If it's nighttime and
    Igor hasn't slept recently, runs the sleep pass and pushes a
    SLEEP_ACTIVE marker to TWM.
    """

    name: str = "sleep_clock"
    TIMING_TIER: str = "slow"

    def __init__(self) -> None:
        super().__init__()
        self._last_sleep_ts: Optional[float] = None
        self._last_check_ts: float = 0.0

    def push(self, cortex: "Cortex") -> list[int]:
        if os.getenv("IGOR_SLEEP_CLOCK", "true").lower() not in ("1", "true", "yes"):
            return []

        now = time.monotonic()
        if now - self._last_check_ts < 60.0:
            return []
        self._last_check_ts = now

        current_hour = datetime.now().hour
        if not _in_sleep_window(current_hour):
            return []

        if self._last_sleep_ts is not None:
            hours_since = (now - self._last_sleep_ts) / 3600.0
            if hours_since < MIN_AWAKE_HOURS:
                return []

        if (
            self._last_sleep_ts is not None
            and (now - self._last_sleep_ts) < MIN_SLEEP_INTERVAL_SEC
        ):
            return []

        return self._run_sleep_pass(cortex, now)

    def _run_sleep_pass(self, cortex: "Cortex", now: float) -> list[int]:
        """Execute the sleep maintenance cycle."""
        self._last_sleep_ts = now
        ids: list[int] = []
        ts = datetime.now(timezone.utc).isoformat()

        try:
            twm_id = cortex.twm_push(
                source="sleep_clock",
                content_csb=f"SLEEP_ACTIVE|clock_triggered|{ts}",
                salience=0.3,
                urgency=0.0,
                ttl_seconds=1800,
                category="sleep_state",
                metadata={"sleep_clock": True, "triggered_at": ts},
            )
            if twm_id:
                ids.append(twm_id)
        except Exception as exc:
            log_error(kind="SLEEP_CLOCK", detail=f"twm_push failed: {exc}")

        try:
            from .sleep_consolidation import SleepConsolidation

            sc = SleepConsolidation()
            sc_ids = sc.push(cortex)
            ids.extend(sc_ids)
            logger.info(
                "[SLEEP_CLOCK] consolidation pass: %d bindings",
                len(sc_ids),
            )
        except Exception as exc:
            log_error(
                kind="SLEEP_CLOCK",
                detail=f"consolidation pass failed: {exc}",
            )

        try:
            cortex.write_ring(
                f"SLEEP_PASS|clock_triggered|{ts}|bindings={len(ids)}",
                category="sleep_trace",
            )
        except Exception as exc:
            log_error(kind="SLEEP_CLOCK", detail=f"ring write failed: {exc}")

        logger.info("[SLEEP_CLOCK] sleep pass complete: %d observations", len(ids))
        return ids

    def last_sleep_age_hours(self) -> Optional[float]:
        """Hours since last sleep pass, or None if never slept."""
        if self._last_sleep_ts is None:
            return None
        return (time.monotonic() - self._last_sleep_ts) / 3600.0
