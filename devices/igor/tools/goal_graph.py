"""
goal_graph.py — T-goals-as-persistent-relationships (#422).

Tools for goal-flavored persistent-relationship facia. Reuses the entire PR
substrate — goal facia are just PR facia with relationship_type in
{goal_aspirational, goal_strategic, goal_tactical} and a small set of
goal-specific metadata fields (desired_future_state, progress, state,
parent_goal_id, requires, blocks).

Igor's insight (2026-04-13): 'A goal has the structure of a persistent
relationship between my current state and a desired future state, with a
gap that needs closing. The difference from a social relationship is mainly
the nature of the second node: instead of Akien or Leah it is state where
Igor can reason about his own goal-graph. Abstract nodes, but nodes.'

State machine:
    not_started → in_progress → completed
                              → blocked
                              → abandoned
    (blocked can return to in_progress)

These tools are CRUD on the goal layer. Frame loading, accretion, and sleep
consolidation all happen via the inherited PR machinery — no new machinery
here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .registry import Tool, registry

logger = logging.getLogger(__name__)


_GOAL_TYPES = {"goal_aspirational", "goal_strategic", "goal_tactical"}
_VALID_STATES = {"not_started", "in_progress", "blocked", "completed", "abandoned"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


def _fetch_goal_facia() -> list[dict]:
    """Return all memories where relationship_type is goal-flavored.

    Reuses _list_facia_memories shape (id, narrative, metadata dict).
    """
    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE memory_type = ? "
                "AND metadata @> jsonb_build_object('facia_role', ?::text) "
                "ORDER BY id",
                ("REFERENCE", "persistent_relationship"),
            ).fetchall()
    except Exception as exc:
        logger.warning("goal_graph _fetch_goal_facia failed: %s", exc)
        return []

    out = []
    for row in rows:
        _id = row[0] if not hasattr(row, "keys") else row["id"]
        _narr = row[1] if not hasattr(row, "keys") else row["narrative"]
        _raw = row[2] if not hasattr(row, "keys") else row["metadata"]
        if isinstance(_raw, dict):
            meta = _raw
        elif isinstance(_raw, str):
            try:
                meta = json.loads(_raw)
            except Exception:
                meta = {}
        else:
            meta = {}
        if meta.get("relationship_type") in _GOAL_TYPES:
            out.append({"id": _id, "narrative": _narr, "metadata": meta})
    return out


def _resolve_goal(name_or_id: str) -> dict | None:
    """Find a goal facia by id or display_name."""
    needle = (name_or_id or "").strip()
    if not needle:
        return None
    low = needle.lower()
    for row in _fetch_goal_facia():
        if row["id"] == needle:
            return row
        if (row["metadata"].get("display_name") or "").lower() == low:
            return row
    return None


def _store_metadata(memory_id: str, metadata: dict) -> bool:
    """Replace metadata on an existing memory. Returns True on success."""
    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            conn.execute(
                "UPDATE memories SET metadata = ? WHERE id = ?",
                (json.dumps(metadata), memory_id),
            )
        return True
    except Exception as exc:
        logger.warning("goal_graph _store_metadata failed for %s: %s", memory_id, exc)
        return False


def _store_memory(memory_id: str, narrative: str, metadata: dict) -> bool:
    """Insert a new goal facia memory row. Returns True on success.

    NOTE: this bypasses cortex.store() which violates the single-chokepoint
    principle (CLAUDE.md: all DB access through cortex). Follow-up:
    T-goal-graph-use-cortex-store will convert this to cortex.store() with
    a proper Memory object. In the meantime, we manually apply the
    test-data tag here so IGOR_TEST_MODE=1 runs still get auto-cleanup.
    """
    from ..memory.test_data_lifecycle import (
        is_test_mode,
        stamp_metadata_for_test_mode,
    )

    if is_test_mode():
        metadata = stamp_metadata_for_test_mode(metadata)
    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            conn.execute(
                "INSERT INTO memories "
                "(id, memory_type, narrative, metadata, timestamp, activation_count) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (memory_id, "REFERENCE", narrative, json.dumps(metadata), _now_iso()),
            )
        return True
    except Exception as exc:
        logger.warning("goal_graph _store_memory failed for %s: %s", memory_id, exc)
        return False


# ── Tool: goal_list ──────────────────────────────────────────────────────────


def goal_list(**_) -> str:
    """List all goal facia grouped by relationship_type with progress + state."""
    rows = _fetch_goal_facia()
    if not rows:
        return "(no goal facia seeded — run seed_strategic_goals.py)"

    by_type: dict[str, list[dict]] = {
        "goal_aspirational": [],
        "goal_strategic": [],
        "goal_tactical": [],
    }
    for row in rows:
        rt = row["metadata"].get("relationship_type")
        if rt in by_type:
            by_type[rt].append(row)

    lines = []
    for rt in ("goal_aspirational", "goal_strategic", "goal_tactical"):
        if not by_type[rt]:
            continue
        lines.append(f"[{rt}]")
        for row in by_type[rt]:
            meta = row["metadata"]
            progress = meta.get("progress", 0.0)
            try:
                progress_f = float(progress)
            except (TypeError, ValueError):
                progress_f = 0.0
            lines.append(
                f"  {row['id']:<40} "
                f"{meta.get('display_name', '?'):<30} "
                f"state={meta.get('state', '?'):<12} "
                f"progress={progress_f:.2f} "
                f"weight={meta.get('cumulative_investment_weight', 0.0):.2f}"
            )
    return "\n".join(lines) if lines else "(no goal facia found)"


# ── Tool: goal_decompose ─────────────────────────────────────────────────────


def goal_decompose(
    parent: str,
    sub_goal_description: str,
    relationship_type: str = "goal_strategic",
    desired_future_state: str = "",
    **_,
) -> str:
    """Create a new goal facia as a child of an existing goal.

    parent: parent goal id or display_name
    sub_goal_description: narrative for the new facia (used as display_name if short)
    relationship_type: goal_aspirational | goal_strategic | goal_tactical
    desired_future_state: what success looks like for the sub-goal
    """
    if relationship_type not in _GOAL_TYPES:
        return f"Invalid relationship_type: {relationship_type!r}. Use one of {sorted(_GOAL_TYPES)}."

    parent_row = _resolve_goal(parent)
    if parent_row is None:
        return f"Parent goal not found: {parent!r}"

    desc_short = (sub_goal_description or "").strip()
    if not desc_short:
        return "Sub-goal description required."

    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    new_id = f"PR_GOAL_{relationship_type.upper().replace('GOAL_', '')}_{ts}"
    display_name = desc_short[:60]

    metadata = {
        "node_kind": "facia",
        "facia_role": "persistent_relationship",
        "parent_facia_id": parent_row["id"],
        "display_name": display_name,
        "relationship_type": relationship_type,
        "status": "active",
        "cumulative_investment_weight": 1.0,
        "last_activity_ts": _now_iso(),
        "description": desc_short,
        "desired_future_state": desired_future_state or desc_short,
        "progress": 0.0,
        "state": "not_started",
        "parent_goal_id": parent_row["id"],
        "requires": [],
        "blocks": [],
        "provenance": f"goal_decompose from {parent_row['id']}",
    }
    narrative = f"[{relationship_type}] {desc_short}"

    if _store_memory(new_id, narrative, metadata):
        return f"Created {new_id} as child of {parent_row['id']}: {display_name}"
    return f"[ERROR] Failed to create sub-goal under {parent_row['id']}"


# ── Tool: goal_progress ──────────────────────────────────────────────────────


def goal_progress(name: str, delta: float = 0.0, **_) -> str:
    """Adjust a goal's progress value by delta. Clamped to [0.0, 1.0]."""
    row = _resolve_goal(name)
    if row is None:
        return f"No goal found for: {name!r}"
    try:
        delta_f = float(delta)
    except (TypeError, ValueError):
        return f"Invalid delta: {delta!r}"

    meta = dict(row["metadata"])
    try:
        current = float(meta.get("progress", 0.0))
    except (TypeError, ValueError):
        current = 0.0
    new_progress = max(0.0, min(1.0, current + delta_f))
    meta["progress"] = new_progress
    meta["last_activity_ts"] = _now_iso()

    if new_progress >= 1.0 and meta.get("state") != "completed":
        meta["state"] = "completed"
    elif new_progress > 0.0 and meta.get("state") == "not_started":
        meta["state"] = "in_progress"

    if _store_metadata(row["id"], meta):
        return (
            f"Updated {row['id']} progress: {current:.2f} → {new_progress:.2f} "
            f"(state={meta['state']})"
        )
    return f"[ERROR] Failed to update {row['id']}"


# ── Tool: goal_state_transition ──────────────────────────────────────────────


_ALLOWED_TRANSITIONS = {
    "not_started": {"in_progress", "abandoned"},
    "in_progress": {"blocked", "completed", "abandoned"},
    "blocked": {"in_progress", "abandoned"},
    "completed": set(),
    "abandoned": set(),
}


def goal_state_transition(name: str, new_state: str, **_) -> str:
    """Move a goal to a new state. Enforces the state machine above."""
    if new_state not in _VALID_STATES:
        return f"Invalid state: {new_state!r}. Use one of {sorted(_VALID_STATES)}."

    row = _resolve_goal(name)
    if row is None:
        return f"No goal found for: {name!r}"

    current = row["metadata"].get("state", "not_started")
    if current not in _ALLOWED_TRANSITIONS:
        return f"[ERROR] {row['id']} has unknown state: {current!r}"
    if new_state == current:
        return f"{row['id']} is already {current}"
    allowed = _ALLOWED_TRANSITIONS[current]
    if new_state not in allowed:
        return (
            f"Invalid transition {current} → {new_state} for {row['id']}. "
            f"Allowed: {sorted(allowed) or '[terminal state]'}"
        )

    meta = dict(row["metadata"])
    meta["state"] = new_state
    meta["last_activity_ts"] = _now_iso()
    if new_state == "completed":
        meta["progress"] = 1.0
    if _store_metadata(row["id"], meta):
        return f"Moved {row['id']}: {current} → {new_state}"
    return f"[ERROR] Failed to update {row['id']}"


# ── Tool registrations ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="goal_list",
        description=(
            "List all goal facia (aspirational, strategic, tactical) with "
            "display name, state, progress, and cumulative investment weight. "
            "Goal facia are persistent-relationship facia with goal-flavored "
            "relationship_type."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=goal_list,
    )
)

registry.register(
    Tool(
        name="goal_decompose",
        description=(
            "Create a new goal facia as a child of an existing goal. Use to "
            "break a strategic goal into sub-goals or a sub-goal into tactical "
            "steps. The new facia inherits PR substrate (frame load, accretion, "
            "consolidation) automatically."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parent": {
                    "type": "string",
                    "description": "Parent goal id or display name.",
                },
                "sub_goal_description": {
                    "type": "string",
                    "description": "Narrative for the new sub-goal.",
                },
                "relationship_type": {
                    "type": "string",
                    "description": "goal_aspirational | goal_strategic | goal_tactical",
                },
                "desired_future_state": {
                    "type": "string",
                    "description": "What success looks like (optional).",
                },
            },
            "required": ["parent", "sub_goal_description"],
        },
        fn=goal_decompose,
    )
)

registry.register(
    Tool(
        name="goal_progress",
        description=(
            "Adjust a goal's progress by delta (clamped to [0.0, 1.0]). "
            "Auto-transitions state to in_progress on first nonzero delta and "
            "to completed when progress reaches 1.0."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Goal id or display name."},
                "delta": {
                    "type": "number",
                    "description": "Progress change (positive or negative).",
                },
            },
            "required": ["name", "delta"],
        },
        fn=goal_progress,
    )
)

registry.register(
    Tool(
        name="goal_state_transition",
        description=(
            "Move a goal to a new state. Valid states: not_started, "
            "in_progress, blocked, completed, abandoned. Enforces the state "
            "machine (e.g. completed/abandoned are terminal)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Goal id or display name."},
                "new_state": {
                    "type": "string",
                    "description": "not_started | in_progress | blocked | completed | abandoned",
                },
            },
            "required": ["name", "new_state"],
        },
        fn=goal_state_transition,
    )
)
