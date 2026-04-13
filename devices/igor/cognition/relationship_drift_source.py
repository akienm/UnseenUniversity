"""
RelationshipDriftSource — quiet-period push source for T-watchlist-relationship-drift.

Runs surface_drifted_relationships from tools/relationship_drift.py during
quiet periods. Same shape as IntentDecaySource, PRConsolidationSource, and
the existing SleepConsolidation source: idle-gated via
cortex._conversation_active_ts, rate-limited so it doesn't fire on every
tick, returns a list of TWM observation IDs.

The watcher does the work (find drifted facia, push them to TWM at
category='relationship_drift'). This module is purely scheduling.
Relationship drift is a slow signal — there's no point firing it more
than a few times an hour.
"""

import time
from datetime import datetime
from typing import Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

# Idle threshold — same value as siblings for consistency.
QUIET_THRESHOLD_SEC = 600  # 10 minutes

# Minimum gap between scans. Relationship drift is the slowest signal of
# the watchlist family — last_activity_ts is measured in days, so checking
# more than once an hour is wasted work.
MIN_INTERVAL_SEC = 3600  # 1 hour


class RelationshipDriftSource(IgorBase):
    """Quiet-period push source for relationship drift detection.

    Implements the push source interface: push(cortex) → list[obs_id].
    """

    name: str = "relationship_drift_source"
    TIMING_TIER: str = "slow"

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        """Run a relationship-drift scan if idle conditions are met."""
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
            from ..tools.relationship_drift import surface_drifted_relationships

            summary = surface_drifted_relationships()
            duration_ms = int((time.monotonic() - t0) * 1000)

            csb = (
                f"RELATIONSHIP_DRIFT_SCAN|complete|ms={duration_ms}"
                f"|summary={summary[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.2,
                urgency=0.1,
                ttl_seconds=600,
                category="relationship_drift_scan",
                metadata={
                    "duration_ms": duration_ms,
                    "summary_excerpt": summary[:500],
                },
            )
            return [obs_id] if obs_id else []
        except Exception as e:
            log_error(
                kind="RELATIONSHIP_DRIFT_SOURCE",
                detail=f"Relationship drift scan failed: {e}",
            )
            return []

    def _is_quiet(self, cortex, now: datetime) -> bool:
        """Same quiet check as the other quiet-period sources."""
        ts = getattr(cortex, "_conversation_active_ts", None)
        if ts is None:
            return True
        elapsed = (now - ts).total_seconds()
        return elapsed >= QUIET_THRESHOLD_SEC
