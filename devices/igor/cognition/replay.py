"""
Consolidation Replay — integrate recently-deposited FACT_CLOUD nodes into graph topology.

Runs as a quiet-period background source: triggered when no user activity for 10 min
AND unprocessed FACT_CLOUD nodes exist.

Purpose (D228): During quiet periods, re-traverse recently deposited FACT_CLOUD nodes
and strengthen co-occurrence edges between them. This consolidates reading deposits and
enables Igor to discuss topics (e.g., Making Money) coherently without requiring
live cloud extraction for every question.

Spec:
  - Trigger: no user activity for 10 min AND unprocessed FACT_CLOUD nodes exist
  - Query: FACT_CLOUD nodes where memory_type='FACTUAL' AND source='cloud_directed'
    AND id LIKE 'FACT_CLOUD_%' AND (last_accessed IS NULL OR last_accessed > last_replay_ts)
  - For each pair of nodes from same reading session (same context_tag OR
    timestamp within 120s): upsert interpretive_edge(src, dst, relation='co_deposited',
    weight+=0.1, cap=1.0)
  - Track progress: PROC_REPLAY_CURSOR memory node stores last_replay_ts
  - Max 50 node-pairs per pass to avoid blocking
  - Log forensically: nodes_processed, edges_created, edges_strengthened

Inertia: LOW (new file, not touching brainstem)
"""

import json
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from dataclasses import dataclass

from ..igor_base import IgorBase
from ..memory.models import Memory, MemoryType
from .forensic_logger import log_error, cts as _cts


@dataclass
class ReplayStats:
    """Track replay pass statistics."""

    nodes_processed: int = 0
    pairs_evaluated: int = 0
    edges_created: int = 0
    edges_strengthened: int = 0
    pass_duration_ms: int = 0


# Define inline to avoid circular imports
class ConsolidationReplay(IgorBase):
    """
    Quiet-period background source that consolidates reading deposits.

    Implements the push source interface: runs in the main loop's push-source cycle,
    rate-limited to avoid blocking. Returns list of TWM observation IDs
    (empty if nothing pushed or no nodes to replay).
    """

    name: str = "consolidation_replay"
    MIN_INTERVAL_SEC = 300  # At least 5 min between replays
    NO_ACTIVITY_THRESHOLD_SEC = 600  # 10 min threshold for "quiet period"
    TIMESTAMP_PROXIMITY_SEC = 120  # Nodes within 120s are co-deposited
    MAX_PAIRS_PER_PASS = 50  # Avoid blocking on large batch
    CURSOR_MEMORY_ID = "PROC_REPLAY_CURSOR"
    EDGE_WEIGHT_DELTA = 0.1
    EDGE_WEIGHT_CAP = 1.0

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None
        self._last_activity_check: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        """
        Run a consolidation replay pass.

        Returns: list of TWM observation IDs (for tracking). Empty if nothing ran.
        """
        now = datetime.now()

        # Rate-limit: don't run more than every MIN_INTERVAL_SEC
        if (
            self._last_run
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []

        # Check for user activity (quiet period gate)
        if not self._is_quiet_period(cortex, now):
            return []

        # Check for unprocessed FACT_CLOUD nodes
        unprocessed_nodes = self._find_unprocessed_nodes(cortex)
        if not unprocessed_nodes:
            return []

        # Run the replay
        start_time = datetime.now()
        stats = self._run_replay(cortex, unprocessed_nodes)
        end_time = datetime.now()
        stats.pass_duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Update cursor
        self._update_cursor(cortex, now)

        # Log forensically
        self._log_pass(stats)

        # Mark run
        self._last_run = now

        # Return empty list (no TWM observations pushed; work was in edge updates)
        # Change to return [obs_id] if we want to push a summary to TWM
        return []

    def _is_quiet_period(self, cortex, now: datetime) -> bool:
        """
        Check if there's been no user activity for NO_ACTIVITY_THRESHOLD_SEC.

        User activity = recent observations in TWM from user input sources.
        Non-user sources (push_sources) don't count as activity.
        """
        try:
            # Query TWM for recent non-push-source observations (last 15 min)
            recent_window = now - timedelta(seconds=900)  # 15 min

            with cortex._conn() as conn:
                # Look for user input or explicit interaction obs
                # (source NOT IN push_sources list)
                row = conn.execute(
                    """
                    SELECT timestamp FROM twm_observations
                    WHERE timestamp > ?
                    AND source NOT IN (
                        'consolidation_replay', 'memory_surfacer', 'boredom',
                        'heartbeat', 'curiosity', 'milieu', 'inbox_watcher',
                        'machines_watcher', 'self_observation'
                    )
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (recent_window.isoformat(),),
                ).fetchone()

            if not row:
                # No user activity in 15 min → quiet period
                return True

            last_activity = datetime.fromisoformat(row[0])
            idle_time = (now - last_activity).total_seconds()

            return idle_time >= self.NO_ACTIVITY_THRESHOLD_SEC

        except Exception as e:
            log_error(f"[ConsolidationReplay] Error checking quiet period: {e}")
            return False

    def _find_unprocessed_nodes(self, cortex) -> List[dict]:
        """
        Query for FACT_CLOUD nodes that haven't been replayed yet.

        Returns list of memory dicts {id, narrative, timestamp, context_of_encoding, metadata}
        """
        try:
            # Get cursor memory to find last replay time
            cursor = cortex.get(self.CURSOR_MEMORY_ID)
            last_replay_ts = None
            if cursor and cursor.metadata.get("last_replay_ts"):
                try:
                    last_replay_ts = datetime.fromisoformat(
                        cursor.metadata["last_replay_ts"]
                    )
                except (ValueError, TypeError):
                    last_replay_ts = None

            with cortex._conn() as conn:
                # Query FACT_CLOUD nodes by ID pattern and source
                query = """
                    SELECT id, narrative, timestamp, context_of_encoding, metadata
                    FROM memories
                    WHERE memory_type = 'FACTUAL'
                    AND source = 'cloud_directed'
                    AND id LIKE 'FACT_CLOUD_%'
                """

                params = []

                # Filter by replay cursor if it exists
                if last_replay_ts:
                    query += " AND timestamp > ?"
                    params.append(last_replay_ts.isoformat())

                query += " ORDER BY timestamp ASC"

                rows = conn.execute(query, params).fetchall()

            return [
                {
                    "id": row[0],
                    "narrative": row[1],
                    "timestamp": datetime.fromisoformat(row[2]) if row[2] else None,
                    "context_of_encoding": row[3],
                    "metadata": json.loads(row[4]) if row[4] else {},
                }
                for row in rows
            ]

        except Exception as e:
            log_error(f"[ConsolidationReplay] Error finding unprocessed nodes: {e}")
            return []

    def _run_replay(self, cortex, nodes: List[dict]) -> ReplayStats:
        """
        Main replay logic: group nodes by context, create co-occurrence edges.

        Returns: ReplayStats with counts
        """
        stats = ReplayStats(nodes_processed=len(nodes))

        if not nodes:
            return stats

        # Group nodes by reading session (context tag or timestamp proximity)
        groups = self._group_by_session(nodes)

        # For each group, create edges between all pairs (up to MAX_PAIRS_PER_PASS)
        pairs_created = 0

        for group in groups:
            if pairs_created >= self.MAX_PAIRS_PER_PASS:
                break

            # Create edges for all pairs in this group
            for i, node_a in enumerate(group):
                for node_b in group[i + 1 :]:
                    if pairs_created >= self.MAX_PAIRS_PER_PASS:
                        break

                    stats.pairs_evaluated += 1

                    # Create or strengthen edge from A → B
                    edge_created = self._upsert_edge(
                        cortex, node_a["id"], node_b["id"], relation="co_deposited"
                    )

                    if edge_created:
                        stats.edges_created += 1
                    else:
                        stats.edges_strengthened += 1

                    pairs_created += 1

        return stats

    def _group_by_session(self, nodes: List[dict]) -> List[List[dict]]:
        """
        Group nodes by reading session.

        Heuristic: same context_of_encoding tag OR timestamps within TIMESTAMP_PROXIMITY_SEC.
        Uses single-pass clustering.
        """
        if not nodes:
            return []

        # Sort by timestamp for proximity clustering
        sorted_nodes = sorted(nodes, key=lambda n: n.get("timestamp") or datetime.min)

        groups = []
        current_group = []
        last_timestamp = None
        last_context_tag = None

        for node in sorted_nodes:
            context = node.get("context_of_encoding", "")
            timestamp = node.get("timestamp")

            # Extract context tag (first part before |)
            context_tag = context.split("|")[0] if context else ""

            # Check if this node belongs in current group
            belongs_in_current = False

            if not current_group:
                belongs_in_current = True
            elif context_tag and context_tag == last_context_tag:
                # Same context tag → same group
                belongs_in_current = True
            elif (
                timestamp
                and last_timestamp
                and (timestamp - last_timestamp).total_seconds()
                <= self.TIMESTAMP_PROXIMITY_SEC
            ):
                # Within time proximity → same group
                belongs_in_current = True

            if belongs_in_current:
                current_group.append(node)
                last_timestamp = timestamp
                last_context_tag = context_tag
            else:
                # Start new group
                if current_group:
                    groups.append(current_group)
                current_group = [node]
                last_timestamp = timestamp
                last_context_tag = context_tag

        if current_group:
            groups.append(current_group)

        return groups

    def _upsert_edge(self, cortex, src_id: str, dst_id: str, relation: str) -> bool:
        """
        Create or strengthen an interpretive edge from src → dst.

        Returns: True if edge was created (new), False if strengthened (existing).
        """
        try:
            src_mem = cortex.get(src_id)
            if not src_mem:
                log_error(f"[ConsolidationReplay] Source node {src_id} not found")
                return False

            # Check if edge already exists
            edge_exists = src_mem.links.get(dst_id) is not None

            # Update or create edge weight
            current_weight = src_mem.links.get(dst_id, 0.0)
            new_weight = min(
                current_weight + self.EDGE_WEIGHT_DELTA, self.EDGE_WEIGHT_CAP
            )

            # Update the source memory with new edge
            src_mem.links[dst_id] = new_weight

            # Store back to DB
            cortex.store(src_mem)

            return not edge_exists  # True if newly created, False if strengthened

        except Exception as e:
            log_error(
                f"[ConsolidationReplay] Error upserting edge {src_id}→{dst_id}: {e}"
            )
            return False

    def _update_cursor(self, cortex, now: datetime) -> None:
        """
        Update the PROC_REPLAY_CURSOR memory with the current timestamp.

        Creates it if it doesn't exist.
        """
        try:
            cursor = cortex.get(self.CURSOR_MEMORY_ID)

            if cursor:
                # Update existing cursor
                cursor.metadata["last_replay_ts"] = now.isoformat()
                cursor.last_accessed = now
            else:
                # Create new cursor
                cursor = Memory(
                    id=self.CURSOR_MEMORY_ID,
                    narrative="Consolidation replay cursor — tracks last replay timestamp",
                    memory_type=MemoryType.PROCEDURAL,
                    source="consolidation_replay",
                    metadata={"last_replay_ts": now.isoformat()},
                )

            cortex.store(cursor)

        except Exception as e:
            log_error(f"[ConsolidationReplay] Error updating cursor: {e}")

    def _log_pass(self, stats: ReplayStats) -> None:
        """Log replay pass statistics forensically."""
        log_msg = (
            f"[ConsolidationReplay] "
            f"nodes_processed={stats.nodes_processed} "
            f"pairs_evaluated={stats.pairs_evaluated} "
            f"edges_created={stats.edges_created} "
            f"edges_strengthened={stats.edges_strengthened} "
            f"duration_ms={stats.pass_duration_ms}"
        )
        # Use Igor's logger (simple print for now; will be picked up by forensic logging)
        print(f"{_cts()} {log_msg}")
