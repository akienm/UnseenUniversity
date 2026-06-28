"""planning.py — Waypoint graph: plan construction + traversal.

WHAT IT IS
──────────
A small layer on top of goal_graph (which already provides per-facia
storage + state machine + decomposition). Adds two operations the goal
graph didn't have:
  - plan_construct(parent_goal_id, waypoints) — bulk-create children
    under a parent with prereq_ids and completion_predicate metadata.
    DAG validated at construction (cycle → raises).
  - plan_traverse(parent_goal_id) -> id | None — topo-sort children;
    return the first whose prereqs are all completed and which is
    itself not completed. Anticipation bus tilts the pick when multiple
    waypoints are ready (T-anticipation-slice3 hook).

WHY IT EXISTS
─────────────
The "want" loop in cognition needs structures over time, not single
points. A waypoint graph makes "current → desired" a sequence of
checkable invariants instead of a vibe. Each waypoint carries a
completion_predicate (description string for v1; executable check
later) so traversal can decide when to advance.

Generalization beyond coding (Akien's "can this be generalized" → CC's
"yes"): same shape covers any task expressible as
  current state → desired state → checkable intermediate invariants.
Coding is one instantiation; cooking, conversation, planning a trip
are others. The data structure doesn't care; only the predicates do.

Substrate, not policy. Construction (which waypoints? in what order?)
is the LLM's job — this module gives the data structure and traversal
mechanics, not the prompts.

DESIGN
──────
Per re-scope 2026-05-03 (Opus chime-in, Akien-approved):
  1. prereq_ids list on each waypoint facia — child of the goal
  2. completion_predicate string per waypoint
  3. plan_traverse — topo-sort by prereq_ids, return first ready
  4. plan_construct — bulk creation with DAG validation
  5. Anticipation bus wiring — when slice 3 lands, plan_traverse reads
     get_bus().top_k() to bias among ready waypoints

A waypoint is just a goal facia (relationship_type=goal_tactical) with
two extra metadata fields: prereq_ids and completion_predicate. Reuses
the existing state machine (not_started → in_progress → completed) and
the existing goal_state_transition tool to advance state.

Updated 2026-05-03.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..igor_base import get_logger
from ..tools import goal_graph
from . import anticipator

log = get_logger(__name__)


# ── Construction ──────────────────────────────────────────────────────────


class PlanCycleError(ValueError):
    """Raised when plan_construct receives waypoints whose prereq_indices
    form a cycle (no valid topological order)."""


def _validate_dag(n: int, prereqs_by_idx: list[list[int]]) -> list[int]:
    """Topological-sort `n` nodes by their integer prereq lists. Returns
    the order; raises PlanCycleError if cyclic."""
    indeg = [0] * n
    for i in range(n):
        for j in prereqs_by_idx[i]:
            if not (0 <= j < n):
                raise PlanCycleError(
                    f"prereq_indices on waypoint {i} references {j} "
                    f"(out of range; n={n})"
                )
            if j == i:
                raise PlanCycleError(f"waypoint {i} has itself as a prereq")
            indeg[i] += 1
    ready = [i for i in range(n) if indeg[i] == 0]
    order: list[int] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for k in range(n):
            if node in prereqs_by_idx[k]:
                indeg[k] -= 1
                if indeg[k] == 0:
                    ready.append(k)
    if len(order) != n:
        cyclic = [i for i in range(n) if indeg[i] > 0]
        raise PlanCycleError(f"prereq cycle detected; nodes still dependent: {cyclic}")
    return order


def plan_construct(
    parent_goal_id: str,
    waypoints: list[dict],
) -> list[str]:
    """Create a waypoint subtree under parent_goal_id.

    Each waypoint dict:
      description (str, required)
      completion_predicate (str, required) — what makes this waypoint done
      prereq_indices (list[int], optional) — earlier entries this depends on
      relationship_type (str, optional) — defaults to "goal_tactical"

    Returns the list of new facia ids in declaration order, so callers
    can map back from index to id when they hold the original list.
    Raises PlanCycleError if the prereq DAG has a cycle.
    """
    if not waypoints:
        return []

    n = len(waypoints)
    prereqs = [list(w.get("prereq_indices") or []) for w in waypoints]

    # Validate DAG upfront — fail fast before any storage side effects.
    _validate_dag(n, prereqs)

    parent_row = goal_graph._resolve_goal(parent_goal_id)
    if parent_row is None:
        raise ValueError(f"Parent goal not found: {parent_goal_id!r}")
    parent_id = parent_row["id"]

    new_ids: list[str] = []
    for w in waypoints:
        description = (w.get("description") or "").strip()
        predicate = (w.get("completion_predicate") or "").strip()
        if not description:
            raise ValueError(f"waypoint missing 'description' (index {len(new_ids)})")
        if not predicate:
            raise ValueError(
                f"waypoint missing 'completion_predicate' (index {len(new_ids)}): "
                f"checkable predicate is the difference between a plan and a vibe"
            )

        rtype = w.get("relationship_type", "goal_tactical")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        new_id = (
            f"PR_GOAL_{rtype.upper().replace('GOAL_', '')}_{ts}_W{len(new_ids):02d}"
        )
        display_name = description[:60]

        metadata = {
            "node_kind": "facia",
            "facia_role": "persistent_relationship",
            "parent_facia_id": parent_id,
            "display_name": display_name,
            "relationship_type": rtype,
            "status": "active",
            "cumulative_investment_weight": 1.0,
            "last_activity_ts": goal_graph._now_iso(),
            "description": description,
            "desired_future_state": predicate,
            "progress": 0.0,
            "state": "not_started",
            "parent_goal_id": parent_id,
            "requires": [],  # filled below after we know all ids
            "blocks": [],
            "provenance": f"plan_construct from {parent_id}",
            # Slice-3 + planning-rescope additions:
            "completion_predicate": predicate,
            "is_waypoint": True,
            "prereq_ids": [],  # filled below
        }
        narrative = f"[{rtype}] {description}"

        if not goal_graph._store_memory(new_id, narrative, metadata):
            raise RuntimeError(
                f"plan_construct: failed to store waypoint {len(new_ids)}"
            )
        new_ids.append(new_id)

    # Second pass: fill prereq_ids now that all ids exist.
    for i, w in enumerate(waypoints):
        prereq_idx = w.get("prereq_indices") or []
        if not prereq_idx:
            continue
        prereq_ids = [new_ids[j] for j in prereq_idx]
        row = goal_graph._resolve_goal(new_ids[i])
        if row is None:
            log.warning(
                "plan_construct: waypoint %s vanished between create and update",
                new_ids[i],
            )
            continue
        meta = dict(row["metadata"])
        meta["prereq_ids"] = prereq_ids
        meta["requires"] = list(prereq_ids)  # mirror to existing field
        goal_graph._store_metadata(new_ids[i], meta)

    return new_ids


# ── Traversal ─────────────────────────────────────────────────────────────


def _children_of(parent_goal_id: str) -> list[dict]:
    """All goal facia whose parent_goal_id == parent_goal_id."""
    parent_row = goal_graph._resolve_goal(parent_goal_id)
    if parent_row is None:
        return []
    parent_id = parent_row["id"]
    return [
        f
        for f in goal_graph._fetch_goal_facia()
        if f["metadata"].get("parent_goal_id") == parent_id
        and f["metadata"].get("is_waypoint") is True
    ]


def _state_completed(facia: dict) -> bool:
    return facia["metadata"].get("state") == "completed"


def plan_traverse(parent_goal_id: str) -> Optional[str]:
    """Return the id of the next actionable waypoint under parent_goal_id.

    A waypoint is actionable when:
      - state != "completed"
      - all prereq_ids reference completed waypoints

    When multiple waypoints are ready, the Anticipation bus tilts the
    pick toward referents currently anticipated with high
    predicted_delta * confidence (slice 3 wiring). With an empty bus
    behavior is identical to strict-first-eligible — anticipation
    steers, doesn't override.

    Returns None when the plan is exhausted (all completed) or when
    nothing is ready (some waypoint blocked by an incomplete prereq
    that isn't itself ready — not a cycle, just timing).
    """
    children = _children_of(parent_goal_id)
    if not children:
        return None

    by_id: dict[str, dict] = {c["id"]: c for c in children}
    completed_ids = {cid for cid, c in by_id.items() if _state_completed(c)}

    ready: list[dict] = []
    for cid, c in by_id.items():
        if cid in completed_ids:
            continue
        prereqs = c["metadata"].get("prereq_ids") or []
        # Only count prereqs whose ids are part of this plan's children;
        # foreign ids are treated as already-satisfied (don't block).
        local_prereqs = [p for p in prereqs if p in by_id]
        if all(p in completed_ids for p in local_prereqs):
            ready.append(c)

    if not ready:
        return None

    # Anticipation bias — pick highest (base_score=0 + bias) among ready.
    # base_score is identical for all ready waypoints today; future work
    # could add cumulative_investment_weight or recency to base_score.
    def _bias(c: dict) -> float:
        return anticipator.anticipation_bias_for_referent(c["id"])

    ready.sort(key=_bias, reverse=True)
    return ready[0]["id"]
