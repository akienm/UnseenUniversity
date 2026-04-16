"""
pr_consolidation.py — T-pr-consolidation.

Offline integration of per-turn accretions into the persistent-relationship's
facia state. The hippocampal-cortical consolidation half of the loop —
walks recent accretions, computes an activity-weighted update, adjusts the
facia's cumulative_investment_weight, refreshes last_activity_ts, and
writes a consolidation_summary memory documenting the pass.

This is the FIRST pass of consolidation. Intentionally minimal:

  - Counts accretions by content_type (exchange / marker / commitment)
  - Computes a weighted activity score (commitment > marker > exchange)
  - Updates cumulative_investment_weight via small bounded deltas
  - Writes a consolidation_summary memory back into the facia subtree
  - Touches last_activity_ts on the facia
  - All best-effort; never raises

DEFERRED to follow-up tickets:

  - Hebbian theme clustering (detect recurring topics across accretions)
  - Duplicate merging via embedding similarity
  - LLM-based theme summarization
  - Pruning of stale low-value accretions
  - Wiring into the existing D353 sleep tick (this sprint exposes the
    function; future ticket integrates it into the cadence)

Biomimetic framing: consolidation is the slow, integrative pass that
turns "we said hello on Tuesday" into "we usually say hello on Tuesdays."
Activity reinforces; absence dims. Weight changes are small and bounded
so a single hot day can't pin a relationship at max, and a single quiet
day can't dim a healthy one. The system favors persistence over reactivity.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from .registry import Tool, registry

# ── Activity weight formula ──────────────────────────────────────────────────

# Per-content-type weights for activity scoring. Commitments are heaviest
# because they encode promises Igor made; markers are mid-weight because
# Akien explicitly flagged them; exchanges are baseline.
_CONTENT_WEIGHTS = {
    "exchange": 1.0,
    "marker": 3.0,
    "commitment": 5.0,
}

# Maximum absolute weight delta per consolidation pass. Small on purpose:
# long-term continuity should accumulate slowly. A relationship needs many
# active passes to climb from 1.0 to 2.0; a single inactive pass barely
# moves the needle.
_MAX_DELTA_PER_PASS = 0.10
_DECAY_PER_INACTIVE_PASS = -0.02
_ACTIVITY_TO_DELTA = 0.01

_MIN_WEIGHT = 0.0
_MAX_WEIGHT = 2.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _consolidation_log(stage: str, **fields) -> None:
    """Forensic log for consolidation. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{_now_iso()} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "pr_consolidation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"pr_consolidation.py:80: {_exc}")


def compute_weight_delta(counts: dict) -> float:
    """Compute the cumulative_investment_weight delta from accretion counts.

    counts: {'exchange': N, 'marker': N, 'commitment': N}

    Active passes (any weighted activity > 0) contribute a positive delta
    proportional to the weighted activity, capped at _MAX_DELTA_PER_PASS.
    Inactive passes contribute _DECAY_PER_INACTIVE_PASS (negative).

    Returns a float in [-_MAX_DELTA_PER_PASS, +_MAX_DELTA_PER_PASS].
    """
    weighted = sum(counts.get(ct, 0) * w for ct, w in _CONTENT_WEIGHTS.items())
    if weighted <= 0:
        return _DECAY_PER_INACTIVE_PASS
    delta = weighted * _ACTIVITY_TO_DELTA
    return min(_MAX_DELTA_PER_PASS, delta)


def _apply_weight_delta(current: float, delta: float) -> float:
    return max(_MIN_WEIGHT, min(_MAX_WEIGHT, current + delta))


def pr_consolidate(facia_id: str, since_ts: Optional[str] = None, **_) -> str:
    """Run a consolidation pass on a single relationship facia.

    Walks accretions linked to facia_id (created on or after since_ts if
    provided; otherwise all accretions known to the facia), counts by
    content_type, computes a weight delta, updates the facia metadata,
    and writes a consolidation_summary memory back into the subtree.

    Returns a human-readable summary string.
    """
    try:
        from . import pr_accretion as _pra
        from . import persistent_relationships as _pr

        facia = _pr._resolve_facia(facia_id)
        if not facia:
            return f"[ERROR] No relationship facia for: {facia_id!r}"

        accretions = _pra.pr_recent_accretions(facia_id, limit=500)

        # Filter by since_ts (string compare on ISO timestamps works).
        if since_ts:
            accretions = [
                a
                for a in accretions
                if a["metadata"].get("accreted_at", "") >= since_ts
            ]

        # Count by content_type
        counts = {"exchange": 0, "marker": 0, "commitment": 0}
        for a in accretions:
            ct = a["metadata"].get("content_type", "")
            if ct in counts:
                counts[ct] += 1

        delta = compute_weight_delta(counts)
        current_weight = float(
            facia["metadata"].get("cumulative_investment_weight", 1.0)
        )
        new_weight = _apply_weight_delta(current_weight, delta)

        # Persist updated weight via the existing CRUD tool — keeps the
        # update path centralized and respects clamping.
        actual_delta = new_weight - current_weight
        if abs(actual_delta) > 1e-9:
            _pr.pr_update_weight(name=facia_id, delta=actual_delta)

        _pr.pr_touch(name=facia_id)

        # Write a consolidation_summary memory into the subtree so the
        # consolidation history is itself part of the relationship.
        summary_text = (
            f"Consolidation pass: {len(accretions)} accretions reviewed "
            f"(exchange={counts['exchange']} marker={counts['marker']} "
            f"commitment={counts['commitment']}). "
            f"weight {current_weight:.3f} → {new_weight:.3f} "
            f"(delta {delta:+.3f}, actual {actual_delta:+.3f})."
        )
        summary_meta = {
            "consolidation_counts": counts,
            "weight_before": current_weight,
            "weight_after": new_weight,
            "weight_delta_proposed": delta,
            "weight_delta_actual": actual_delta,
            "since_ts": since_ts or "",
            "accretions_reviewed": len(accretions),
        }
        summary_id = _pra.pr_accrete(
            facia_id=facia_id,
            content_type="consolidation_summary",
            narrative=summary_text,
            metadata=summary_meta,
        )

        _consolidation_log(
            "pass",
            facia_id=facia_id,
            reviewed=len(accretions),
            counts=counts,
            weight_before=f"{current_weight:.3f}",
            weight_after=f"{new_weight:.3f}",
            summary_id=summary_id or "",
        )

        return summary_text
    except Exception as e:
        _consolidation_log("pass_failed", facia_id=facia_id, error=str(e))
        return f"[ERROR] pr_consolidate: {e}"


def pr_consolidate_all(since_ts: Optional[str] = None, **_) -> str:
    """Run consolidation on every active persistent-relationship facia.

    Skips dormant and archived relationships — they shouldn't accumulate
    weight from inactive passes; their dimming should be deliberate, not
    incidental.
    """
    try:
        from . import persistent_relationships as _pr

        facia_rows = _pr._list_facia_memories()
        if facia_rows and isinstance(facia_rows[0], dict) and "error" in facia_rows[0]:
            return f"[ERROR] {facia_rows[0]['error']}"

        results = []
        for row in facia_rows:
            if row["metadata"].get("status") != "active":
                continue
            # T-goals-as-persistent-relationships (#422): goal-flavored facia
            # reuse the PR substrate but their consolidation semantics are
            # progress/state-based, not accretion-based. Skip them here until
            # a dedicated goal consolidation path exists.
            rtype = row["metadata"].get("relationship_type", "")
            if isinstance(rtype, str) and rtype.startswith("goal_"):
                continue
            facia_id = row["id"]
            summary = pr_consolidate(facia_id=facia_id, since_ts=since_ts)
            results.append(f"  {facia_id}: {summary}")

        if not results:
            return "(no active relationships to consolidate)"
        return "Consolidation pass on all active relationships:\n" + "\n".join(results)
    except Exception as e:
        _consolidation_log("all_failed", error=str(e))
        return f"[ERROR] pr_consolidate_all: {e}"


# ── Tool registrations ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="pr_consolidate",
        description=(
            "Run a consolidation pass on one persistent-relationship facia. "
            "Walks recent accretions, counts by content_type (exchange/marker/"
            "commitment), updates cumulative_investment_weight via small bounded "
            "deltas, refreshes last_activity_ts, and writes a consolidation_summary "
            "memory into the facia subtree."
        ),
        parameters={
            "type": "object",
            "properties": {
                "facia_id": {
                    "type": "string",
                    "description": "Relationship facia id (e.g. 'PR_AKIEN')",
                },
                "since_ts": {
                    "type": "string",
                    "description": (
                        "ISO timestamp; only accretions on or after this time "
                        "are reviewed. Optional — omit to review all known "
                        "accretions for this facia."
                    ),
                },
            },
            "required": ["facia_id"],
        },
        fn=pr_consolidate,
    )
)


registry.register(
    Tool(
        name="pr_consolidate_all",
        description=(
            "Run consolidation on every active persistent-relationship facia. "
            "Skips dormant and archived relationships."
        ),
        parameters={
            "type": "object",
            "properties": {
                "since_ts": {
                    "type": "string",
                    "description": (
                        "ISO timestamp; only accretions on or after this time "
                        "are reviewed across all active relationships. Optional."
                    ),
                },
            },
            "required": [],
        },
        fn=pr_consolidate_all,
    )
)
