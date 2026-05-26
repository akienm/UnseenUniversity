"""
persistent_relationships.py — T-pr-schema-seed.

CRUD tools over the persistent-relationships tree. A persistent-relationship
is the structural unit of long-term conversational continuity (Akien framing,
2026-04-13). It can be with a person, project, subject, or avocation. Active
or dormant. Collectively the set of persistent-relationships IS the narrative
of a life.

Each persistent-relationship is a facia memory under the PR_ROOT facia, with
metadata carrying status, cumulative_investment_weight, last_activity_ts,
display_name, relationship_type, description.

This module is structural CRUD only. Loading the matching facia as the
primary TWM attractor on a turn — the architectural hinge — is
T-pr-load-as-primary-attractor. Per-turn accretion is T-pr-accretion. Sleep
consolidation is T-pr-consolidation. Weight propagation is
T-pr-investment-weight-propagation. Goal nesting is
T-pr-secondary-attractor-nesting.
"""

from datetime import datetime, timezone
from typing import Optional

from devices.igor.tools.registry import Tool, registry

# ── T-pr-investment-weight-propagation ───────────────────────────────────────

# Frame salience modulation. The frame marker is ambient backdrop; its
# salience varies in a small range as a function of the relationship's
# cumulative_investment_weight. Range is intentionally narrow so a
# high-weight relationship is more "felt" without ever competing with
# foreground tasks (which sit at ~0.85+).
#
#   weight 0.0 (fully dormant)  → salience 0.70
#   weight 1.0 (baseline)       → salience 0.75
#   weight 2.0 (saturated)      → salience 0.80
#
# Foreground tasks (~0.85-0.95) and user input (~0.95) always dominate.
# The frame conditions routing without competing for attention.
_FRAME_BASE_SALIENCE = 0.75
_FRAME_WEIGHT_SCALE = 0.05
_FRAME_SALIENCE_MIN = 0.70
_FRAME_SALIENCE_MAX = 0.80


def pr_compute_frame_salience(weight: float) -> float:
    """Map a relationship's cumulative_investment_weight to a frame salience.

    Pure formula. Weight 1.0 (baseline) → 0.75 (default frame salience).
    Higher-weight relationships get a small boost; lower-weight ones get
    a small penalty. Result is clamped to [0.70, 0.80] so the frame never
    invades the foreground-task salience band (~0.85+).
    """
    try:
        w = float(weight)
    except (TypeError, ValueError):
        w = 1.0
    raw = _FRAME_BASE_SALIENCE + (w - 1.0) * _FRAME_WEIGHT_SCALE
    return max(_FRAME_SALIENCE_MIN, min(_FRAME_SALIENCE_MAX, raw))


# T-pr-retrieval-bias: small additive bonus applied to memories whose
# metadata.pr_facia_id matches the active relationship frame in TWM.
# The bonus magnitude scales with the facia's cumulative_investment_weight.
# Range is small — relationship presence is a tiebreaker, not an override.
# Strong text/embedding signals still win on their merits.
_BIAS_BASE = 0.10
_BIAS_WEIGHT_SCALE = 0.05
_BIAS_MIN = 0.05
_BIAS_MAX = 0.20


def pr_compute_retrieval_bias(weight: float) -> float:
    """Map a relationship's cumulative_investment_weight to an additive
    retrieval bias. Weight 1.0 (baseline) → 0.10 default bonus.

    Range: [0.05, 0.20]. Even a fully-dormant relationship (weight 0.0)
    keeps a small 0.05 bonus — past relationships still slightly color
    retrieval; they aren't erased. Saturated relationships (weight 2.0+)
    cap at 0.20 so they nudge retrieval without overwhelming text signals.
    """
    try:
        w = float(weight)
    except (TypeError, ValueError):
        w = 1.0
    raw = _BIAS_BASE + (w - 1.0) * _BIAS_WEIGHT_SCALE
    return max(_BIAS_MIN, min(_BIAS_MAX, raw))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


def _list_facia_memories() -> list:
    """Return all memories with metadata.facia_role == 'persistent_relationship'.

    Uses cortex._conn() (the home DB where memories live) and the
    db_proxy's execute interface (? placeholders auto-translate to %s).
    """
    import json as _json

    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE memory_type = %s "
                "AND metadata @> jsonb_build_object('facia_role', %s::text) "
                "ORDER BY id",
                ("REFERENCE", "persistent_relationship"),
            ).fetchall()
    except Exception as e:
        return [{"error": f"_list_facia_memories: {e}"}]

    out = []
    for row in rows:
        _id = row[0] if not hasattr(row, "keys") else row["id"]
        _narr = row[1] if not hasattr(row, "keys") else row["narrative"]
        _raw_meta = row[2] if not hasattr(row, "keys") else row["metadata"]
        if isinstance(_raw_meta, dict):
            _meta = _raw_meta
        elif isinstance(_raw_meta, str):
            try:
                _meta = _json.loads(_raw_meta)
            except Exception:
                _meta = {}
        else:
            _meta = {}
        out.append({"id": _id, "narrative": _narr, "metadata": _meta})
    return out


def resolve_facia_by_author(author: str) -> str | None:
    """T-pr-interlocutor-resolution: look up which persistent-relationship
    facia is associated with an author handle.

    Walks all relationship facia and checks each one's metadata.author_handles
    list (case-insensitive) for the incoming author. Returns the facia id
    on match, or None if no facia claims that author.

    This is the lookup used by _resolve_relationship_frame in main.py to
    map an incoming turn's author to its persistent-relationship frame.
    Multiple authors can map to the same facia (e.g. 'akien' and
    'claude-code' both → PR_AKIEN). Unknown authors return None and no
    frame is loaded — Igor still functions, just without relationship
    context for that turn.
    """
    if not author or not isinstance(author, str):
        return None
    needle = author.lower().strip()
    if not needle:
        return None
    for row in _list_facia_memories():
        if "error" in row:
            return None
        meta = row.get("metadata") or {}
        handles = meta.get("author_handles") or []
        if not isinstance(handles, list):
            continue
        for h in handles:
            if isinstance(h, str) and h.lower() == needle:
                return row["id"]
    return None


def _resolve_facia(name_or_id: str):
    """Find a relationship facia by id ('PR_AKIEN'), display_name ('Akien'),
    or the trimmed lowercase variant. Returns the row dict or None."""
    needle = (name_or_id or "").strip()
    if not needle:
        return None
    needle_lower = needle.lower()
    for row in _list_facia_memories():
        if "error" in row:
            return None
        if row["id"] == needle:
            return row
        meta = row["metadata"]
        if (meta.get("display_name") or "").lower() == needle_lower:
            return row
        # Allow 'akien' to match 'PR_AKIEN'
        if row["id"].lower() == f"pr_{needle_lower}":
            return row
    return None


def _store_facia_metadata(memory_id: str, new_metadata: dict) -> bool:
    """Replace the metadata on a facia memory in place. Returns True on success."""
    import json as _json

    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            conn.execute(
                "UPDATE memories SET metadata = %s WHERE id = %s",
                (_json.dumps(new_metadata), memory_id),
            )
        return True
    except Exception:
        return False


# ── Tool functions ───────────────────────────────────────────────────────────


def pr_list(**_) -> str:
    """List all persistent-relationships with status and cumulative weight."""
    rows = _list_facia_memories()
    if not rows:
        return "(no persistent-relationships seeded — run seed_persistent_relationships.py)"
    if rows and "error" in rows[0]:
        return f"[ERROR] {rows[0]['error']}"
    lines = ["Persistent relationships:"]
    for row in rows:
        meta = row["metadata"]
        lines.append(
            f"  {row['id']:<30} "
            f"{meta.get('display_name', '?'):<25} "
            f"type={meta.get('relationship_type', '?'):<10} "
            f"status={meta.get('status', '?'):<10} "
            f"weight={meta.get('cumulative_investment_weight', 0.0):.2f} "
            f"last_active={meta.get('last_activity_ts', 'never')[:19]}"
        )
    return "\n".join(lines)


def pr_get(name: str, **_) -> str:
    """Get a single persistent-relationship's facia memory by id or display_name."""
    row = _resolve_facia(name)
    if not row:
        return f"No persistent-relationship found for: {name!r}"
    meta = row["metadata"]
    out = [
        f"id: {row['id']}",
        f"narrative: {row['narrative']}",
        f"display_name: {meta.get('display_name', '')}",
        f"relationship_type: {meta.get('relationship_type', '')}",
        f"status: {meta.get('status', '')}",
        f"cumulative_investment_weight: {meta.get('cumulative_investment_weight', 0.0):.3f}",
        f"last_activity_ts: {meta.get('last_activity_ts', 'never')}",
        f"description: {meta.get('description', '')}",
        f"parent_facia_id: {meta.get('parent_facia_id', '')}",
    ]
    return "\n".join(out)


def pr_touch(name: str, **_) -> str:
    """Update last_activity_ts on a relationship facia to now."""
    row = _resolve_facia(name)
    if not row:
        return f"No persistent-relationship found for: {name!r}"
    meta = dict(row["metadata"])
    meta["last_activity_ts"] = _now_iso()
    if _store_facia_metadata(row["id"], meta):
        return f"Touched {row['id']} at {meta['last_activity_ts']}"
    return f"[ERROR] Failed to update {row['id']}"


def pr_set_status(name: str, status: str, **_) -> str:
    """Set status on a relationship facia. Valid: active | dormant | archived."""
    if status not in ("active", "dormant", "archived"):
        return f"Invalid status: {status!r}. Use active|dormant|archived."
    row = _resolve_facia(name)
    if not row:
        return f"No persistent-relationship found for: {name!r}"
    meta = dict(row["metadata"])
    meta["status"] = status
    if _store_facia_metadata(row["id"], meta):
        return f"Set {row['id']} status={status}"
    return f"[ERROR] Failed to update {row['id']}"


def pr_update_weight(name: str, delta: float, **_) -> str:
    """Adjust cumulative_investment_weight by delta. Clamped to [0.0, 2.0]."""
    row = _resolve_facia(name)
    if not row:
        return f"No persistent-relationship found for: {name!r}"
    meta = dict(row["metadata"])
    current = float(meta.get("cumulative_investment_weight", 0.0))
    try:
        delta_f = float(delta)
    except (TypeError, ValueError):
        return f"Invalid delta: {delta!r}"
    new_weight = max(0.0, min(2.0, current + delta_f))
    meta["cumulative_investment_weight"] = new_weight
    if _store_facia_metadata(row["id"], meta):
        return f"Updated {row['id']} weight: {current:.3f} → {new_weight:.3f}"
    return f"[ERROR] Failed to update {row['id']}"


# ── Tool registrations ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="pr_list",
        description=(
            "List all persistent-relationships (the structural unit of long-term "
            "conversational continuity) with status, type, weight, and last activity."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=pr_list,
    )
)

registry.register(
    Tool(
        name="pr_get",
        description=(
            "Get the full facia metadata for one persistent-relationship by id "
            "(e.g. 'PR_AKIEN') or display name (e.g. 'Akien')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Relationship id or display name",
                },
            },
            "required": ["name"],
        },
        fn=pr_get,
    )
)

registry.register(
    Tool(
        name="pr_touch",
        description="Update last_activity_ts on a relationship facia to now.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Relationship id or display name",
                },
            },
            "required": ["name"],
        },
        fn=pr_touch,
    )
)

registry.register(
    Tool(
        name="pr_set_status",
        description=(
            "Set status on a relationship facia. Valid values: active, dormant, archived. "
            "Dormant relationships stay in the tree (never deleted) so reactivation is possible."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Relationship id or display name",
                },
                "status": {
                    "type": "string",
                    "description": "active | dormant | archived",
                },
            },
            "required": ["name", "status"],
        },
        fn=pr_set_status,
    )
)

registry.register(
    Tool(
        name="pr_update_weight",
        description=(
            "Adjust a relationship's cumulative_investment_weight by delta. "
            "Clamped to [0.0, 2.0]. Used by accretion/consolidation passes to "
            "track how much investment a relationship currently carries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Relationship id or display name",
                },
                "delta": {
                    "type": "number",
                    "description": "Weight change (positive or negative)",
                },
            },
            "required": ["name", "delta"],
        },
        fn=pr_update_weight,
    )
)
