"""
PRConsolidationSource — quiet-period push source for persistent-relationships
consolidation. Follows the same pattern as SleepConsolidation (D353): idle-
gated, rate-limited, returns a list of TWM observation IDs.

Biomimetic framing: hippocampal-cortical consolidation happens during sleep,
not while you're paying attention. Igor's relationship-level consolidation
follows the same rhythm. SleepConsolidation does the Hebbian binding work
during quiet periods; this is the sibling operation that walks the day's
relationship accretions and updates each facia's cumulative_investment_weight,
running themes, etc. Both fire in the same quiet window from different
push-source slots.

The actual consolidation logic lives in tools/pr_consolidation.py — this
module is purely scheduling. pr_consolidate_all is idempotent and best-
effort, so re-runs are safe and a failure here never breaks the main loop.
"""

import time
from datetime import datetime
from typing import Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

# ── Constants ────────────────────────────────────────────────────────────────

# Idle threshold — must be at least this long since the last user input
# before consolidation is allowed to fire. Same value as SleepConsolidation.
QUIET_THRESHOLD_SEC = 600  # 10 minutes

# Minimum gap between consolidation passes. PR consolidation is cheap (counts
# accretions, computes weight delta) but we don't need it firing constantly.
# 30 minutes between passes balances "stays current" with "doesn't churn".
MIN_INTERVAL_SEC = 1800  # 30 minutes


class PRConsolidationSource(IgorBase):
    """
    Quiet-period push source that runs pr_consolidate_all on all active
    persistent-relationship facia.

    Implements the push source interface: push(cortex) → list[obs_id].
    """

    name: str = "pr_consolidation_source"
    TIMING_TIER: str = "slow"

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        """Run a PR consolidation pass if idle conditions are met."""
        now = datetime.now()

        # Rate limit
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < MIN_INTERVAL_SEC
        ):
            return []

        # Idle gate — skip during active conversation
        if not self._is_quiet(cortex, now):
            return []

        self._last_run = now
        t0 = time.monotonic()

        try:
            from ..tools.pr_consolidation import pr_consolidate_all

            summary = pr_consolidate_all()
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Surface a single low-salience marker so the consolidation pass
            # is observable in TWM without being attentionally noisy.
            csb = (
                f"PR_CONSOLIDATION|pass_complete|ms={duration_ms}"
                f"|summary={summary[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.2,
                urgency=0.1,
                ttl_seconds=600,
                category="pr_consolidation_pass",
                metadata={
                    "duration_ms": duration_ms,
                    "summary_excerpt": summary[:500],
                },
            )
            return [obs_id] if obs_id else []
        except Exception as e:
            log_error(
                kind="PR_CONSOLIDATION_SOURCE",
                detail=f"PR consolidation pass failed: {e}",
            )
            return []

    def _is_quiet(self, cortex, now: datetime) -> bool:
        """Check if system is in quiet period — same gate as SleepConsolidation.

        Conversation_active_ts is None on a fresh boot (no conversation yet);
        treat that as quiet so a freshly-booted Igor still consolidates if
        relationships have accreted state from a prior session.
        """
        ts = getattr(cortex, "_conversation_active_ts", None)
        if ts is None:
            return True
        elapsed = (now - ts).total_seconds()
        return elapsed >= QUIET_THRESHOLD_SEC
