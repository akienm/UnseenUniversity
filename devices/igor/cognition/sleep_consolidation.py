"""
Sleep Consolidation — idle-time network wandering that discovers and
strengthens missing bindings between co-activated memory nodes.

Biology: hippocampal replay during quiet wakefulness and sleep consolidates
recent experiences by replaying activation patterns and strengthening synaptic
connections between co-activated neurons. This module does the same for Igor's
memory graph.

Algorithm:
  1. Detect idle period (no user input for QUIET_THRESHOLD_SEC)
  2. Query recent traces for node pairs that co-activated in search results
  3. For each pair: check if a direct link exists
  4. Missing links → create weak edge (Hebbian binding discovery)
  5. Existing weak links → strengthen (consolidation)
  6. Log forensically for observability

Trigger: push source in main loop, fires during quiet periods.
Complements T-twm-attentional-gating: the gate suppresses background noise
during conversation; sleep consolidation uses the quiet periods productively.

Inertia: LOW (new file, not touching brainstem)
"""

import json
import time
from datetime import datetime, timedelta
from typing import Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error, cts as _cts

# ── Constants ────────────────────────────────────────────────────────────────

QUIET_THRESHOLD_SEC = 600  # 10 min no user activity = quiet period
MIN_INTERVAL_SEC = 300  # At least 5 min between consolidation passes
TRACE_WINDOW_HOURS = 24  # Look at traces from last 24 hours
MAX_PAIRS_PER_PASS = 40  # Cap to avoid blocking main loop
MIN_COACTIVATION_COUNT = 2  # Pair must co-appear in >= N traces to matter
BINDING_WEIGHT = 0.08  # Initial edge weight for discovered bindings
STRENGTHEN_DELTA = 0.05  # Weight increment for existing weak edges
STRENGTHEN_CAP = 0.6  # Don't strengthen beyond this (strong links are earned)
MIN_HEAT_THRESHOLD = 0.01  # Skip cold nodes (not recently relevant)


class SleepConsolidation(IgorBase):
    """
    Quiet-period push source that discovers and strengthens missing bindings.

    Implements the push source interface: returns list of TWM observation IDs.
    """

    name: str = "sleep_consolidation"
    TIMING_TIER: str = "slow"

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        """Run a sleep consolidation pass if idle conditions are met."""
        now = datetime.now()

        # Rate limit
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < MIN_INTERVAL_SEC
        ):
            return []

        # Check idle: conversation gate timestamp on cortex
        if not self._is_quiet(cortex, now):
            return []

        self._last_run = now
        t0 = time.monotonic()

        try:
            pairs = self._find_coactivated_pairs(cortex, now)
            if not pairs:
                return []

            created, strengthened, skipped = self._bind_pairs(cortex, pairs)

            duration_ms = int((time.monotonic() - t0) * 1000)
            self._log_pass(len(pairs), created, strengthened, skipped, duration_ms)

            if created > 0 or strengthened > 0:
                csb = (
                    f"SLEEP_CONSOLIDATION|pass_complete"
                    f"|pairs={len(pairs)}|created={created}"
                    f"|strengthened={strengthened}|skipped={skipped}"
                    f"|ms={duration_ms}"
                )
                obs_id = cortex.twm_push(
                    source=self.name,
                    content_csb=csb,
                    salience=0.2,
                    urgency=0.1,
                    ttl_seconds=600,
                    metadata={
                        "pairs_evaluated": len(pairs),
                        "edges_created": created,
                        "edges_strengthened": strengthened,
                    },
                )
                return [obs_id]

        except Exception as e:
            log_error(
                kind="SLEEP_CONSOLIDATION_ERROR",
                detail=f"sleep_consolidation pass failed: {e}",
            )

        return []

    def _is_quiet(self, cortex, now: datetime) -> bool:
        """Check if system is in quiet period (no recent user conversation)."""
        ts = getattr(cortex, "_conversation_active_ts", None)
        if ts is None:
            # No conversation ever — treat as quiet (boot idle)
            return True
        elapsed = (now - ts).total_seconds()
        return elapsed >= QUIET_THRESHOLD_SEC

    def _find_coactivated_pairs(
        self, cortex, now: datetime
    ) -> list[tuple[str, str, int]]:
        """
        Find node pairs that co-appeared in recent search traces but may lack
        direct links.

        Returns: list of (node_a, node_b, coactivation_count) sorted by count desc.
        """
        cutoff = (now - timedelta(hours=TRACE_WINDOW_HOURS)).isoformat()

        try:
            with cortex._conn() as conn:
                rows = conn.execute(
                    "SELECT nodes FROM traces WHERE recorded_at > %s "
                    "ORDER BY recorded_at DESC LIMIT 200",
                    (cutoff,),
                ).fetchall()
        except Exception:
            return []

        if not rows:
            return []

        # Count how many traces each pair appears in together
        pair_counts: dict[tuple[str, str], int] = {}
        for row in rows:
            try:
                nodes = json.loads(row["nodes"] if isinstance(row, dict) else row[0])
            except (json.JSONDecodeError, TypeError, IndexError):
                continue

            # Extract node IDs from this trace
            node_ids = []
            for n in nodes:
                nid = n.get("node_id") if isinstance(n, dict) else None
                if nid:
                    node_ids.append(nid)

            # Count unique pairs (order-independent)
            seen = set()
            for i, a in enumerate(node_ids):
                for b in node_ids[i + 1 :]:
                    pair = tuple(sorted([a, b]))
                    if pair not in seen:
                        seen.add(pair)
                        pair_counts[pair] = pair_counts.get(pair, 0) + 1

        # Filter to pairs with enough co-activations
        candidates = [
            (a, b, count)
            for (a, b), count in pair_counts.items()
            if count >= MIN_COACTIVATION_COUNT
        ]

        # Sort by co-activation count (strongest signal first), cap
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:MAX_PAIRS_PER_PASS]

    def _bind_pairs(
        self, cortex, pairs: list[tuple[str, str, int]]
    ) -> tuple[int, int, int]:
        """
        For each co-activated pair, create or strengthen bindings.

        Returns: (edges_created, edges_strengthened, skipped)
        """
        created = 0
        strengthened = 0
        skipped = 0

        for node_a, node_b, coact_count in pairs:
            try:
                mem_a = cortex.get(node_a)
                mem_b = cortex.get(node_b)
                if mem_a is None or mem_b is None:
                    skipped += 1
                    continue

                # Check existing link weight (bidirectional)
                weight_ab = mem_a.links.get(node_b, 0.0)
                weight_ba = mem_b.links.get(node_a, 0.0)
                existing = max(weight_ab, weight_ba)

                if existing >= STRENGTHEN_CAP:
                    skipped += 1  # Already well-connected
                    continue

                if existing == 0.0:
                    # No link exists — create binding discovery edge
                    # Scale initial weight by co-activation strength
                    weight = min(STRENGTHEN_CAP, BINDING_WEIGHT * min(coact_count, 5))
                    cortex.reinforce_links(node_a, [node_b], weight)
                    cortex.reinforce_links(node_b, [node_a], weight)
                    created += 1
                else:
                    # Weak link exists — strengthen it
                    delta = min(STRENGTHEN_DELTA, STRENGTHEN_CAP - existing)
                    if delta > 0.005:
                        cortex.reinforce_links(node_a, [node_b], delta)
                        cortex.reinforce_links(node_b, [node_a], delta)
                        strengthened += 1
                    else:
                        skipped += 1

            except Exception as e:
                log_error(
                    kind="SLEEP_CONSOLIDATION_BIND_ERROR",
                    detail=f"bind {node_a}<->{node_b}: {e}",
                )
                skipped += 1

        return created, strengthened, skipped

    def _log_pass(
        self,
        pairs: int,
        created: int,
        strengthened: int,
        skipped: int,
        duration_ms: int,
    ) -> None:
        """Forensic log of consolidation pass."""
        try:
            from .forensic_logger import log_anomaly

            log_anomaly(
                kind="SLEEP_CONSOLIDATION_PASS",
                detail=(
                    f"pairs={pairs} created={created} "
                    f"strengthened={strengthened} skipped={skipped} "
                    f"ms={duration_ms}"
                ),
            )
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"sleep_consolidation.py:260: {_exc}")
