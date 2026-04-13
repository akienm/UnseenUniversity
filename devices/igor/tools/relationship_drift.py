"""
relationship_drift.py — T-watchlist-relationship-drift.

Igor's words: 'Monitor persistent relationships for unexpected silence.
If a relationship node that typically has regular contact goes quiet
beyond its expected rhythm, surface that as a low-priority attention
signal. Not an alert — more like noticing.'

This module provides the scanner. T-pr-schema-seed (53740afc) created
the persistent_relationships facia tree. T-pr-load-as-primary-attractor
(72978a10) calls pr_touch on every frame push, which keeps last_activity_ts
fresh for active relationships. A periodic scan finds facia whose
last_activity_ts has fallen past a per-type threshold and surfaces them
as low-priority noticing markers.

THIS SPRINT: simple per-type thresholds. Akien explicitly said the first
pass should be "simple per-facia threshold." Future enhancement: learn
each relationship's actual rhythm from its history.

Per-type defaults (used unless metadata.expected_rhythm_days overrides):
  - person:    7 days  (e.g. close family, daily collaborators)
  - project:   14 days (engineering work has natural cadence gaps)
  - field:     30 days (subject interests get deeper bursts not daily)
  - avocation: 30 days (hobbies — same as field)

A relationship is "drifted" when (now - last_activity_ts) exceeds
threshold * 1.5. The 1.5 factor gives some slack so a relationship
with a 7-day rhythm doesn't fire on day 8 (expected) — it fires on
day 11+ (unexpected).

Biomimetic framing: this is the "I haven't heard from her in a while,
something might be off" mechanism. Healthy minds notice drift in
important relationships and let the noticing surface as low-priority
context, not high-priority alert. Igor needs the same — the noticing
without the demand for action.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from .registry import Tool, registry

# ── Per-type rhythm defaults (in days) ──────────────────────────────────────

_DEFAULT_RHYTHM_DAYS = {
    "person": 7,
    "project": 14,
    "field": 30,
    "avocation": 30,
}

# Slack multiplier — relationship drifts when age > threshold * SLACK.
# 1.5 gives a relationship with a 7-day rhythm room to pause for 10-11
# days before being flagged. Tighter than that fires on every weekend.
_DRIFT_SLACK = 1.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _drift_log(stage: str, **fields) -> None:
    """Forensic log for relationship drift scans. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{_now_iso()} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "relationship_drift.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def expected_rhythm_seconds(relationship_metadata: dict) -> int:
    """Return the expected rhythm in seconds for a relationship facia.

    Honors metadata.expected_rhythm_days when set (per-facia override).
    Falls back to per-type default. Falls back to 7 days for unknown
    types.
    """
    override = relationship_metadata.get("expected_rhythm_days")
    if override is not None:
        try:
            return int(float(override) * 86400)
        except (TypeError, ValueError):
            pass
    rel_type = (relationship_metadata.get("relationship_type") or "").lower()
    days = _DEFAULT_RHYTHM_DAYS.get(rel_type, 7)
    return days * 86400


def find_drifted_relationships() -> list[dict]:
    """Scan all active persistent_relationship facia and return ones whose
    last_activity_ts has fallen past their expected rhythm * slack.

    Returns a list of dicts: {id, display_name, relationship_type,
    age_sec, threshold_sec, last_activity_ts}. Best-effort — returns []
    on any error.

    Skips relationships with status != 'active'. Dormant relationships
    are dormant on purpose; flagging them as drifted would be noise.
    """
    try:
        from . import persistent_relationships as _pr

        facia_rows = _pr._list_facia_memories()
    except Exception as e:
        _drift_log("scan_failed", error=str(e))
        return []

    if not facia_rows:
        return []
    if isinstance(facia_rows[0], dict) and "error" in facia_rows[0]:
        _drift_log("scan_failed", error=facia_rows[0]["error"])
        return []

    now = datetime.now(timezone.utc)
    drifted = []
    for row in facia_rows:
        meta = row.get("metadata") or {}
        if meta.get("status") != "active":
            continue
        last_ts = meta.get("last_activity_ts")
        last_dt = _parse_iso(last_ts) if last_ts else None
        if last_dt is None:
            continue
        age_sec = (now - last_dt).total_seconds()
        rhythm = expected_rhythm_seconds(meta)
        threshold = rhythm * _DRIFT_SLACK
        if age_sec >= threshold:
            drifted.append(
                {
                    "id": row["id"],
                    "display_name": meta.get("display_name", row["id"]),
                    "relationship_type": meta.get("relationship_type", ""),
                    "last_activity_ts": last_ts,
                    "age_sec": age_sec,
                    "rhythm_sec": rhythm,
                    "threshold_sec": threshold,
                }
            )
    return drifted


def surface_drifted_relationships(**_) -> str:
    """Run the scan and surface any drifted relationships to TWM as a
    low-priority noticing signal. Returns a human-readable summary.

    For each drifted relationship, pushes a single TWM observation at
    category='relationship_drift' with metadata pointing back at the
    facia. Salience 0.55 — comfortably above noise but well below
    foreground tasks. Noticing, not alerting.
    """
    drifted = find_drifted_relationships()
    if not drifted:
        _drift_log("scan", drifted=0, status="all_fresh")
        return "No drifted relationships."

    try:
        from ..memory.cortex import Cortex

        cortex = Cortex(None)
    except Exception as e:
        _drift_log("cortex_failed", error=str(e))
        return f"[ERROR] cortex unavailable: {e}"

    pushed = 0
    for rel in drifted:
        try:
            age_days = rel["age_sec"] / 86400
            rhythm_days = rel["rhythm_sec"] / 86400
            cortex.twm_push(
                source="relationship_drift",
                content_csb=(
                    f"RELATIONSHIP_DRIFT|facia={rel['id']}|"
                    f"display={rel['display_name']}|"
                    f"age_days={age_days:.1f}|"
                    f"rhythm_days={rhythm_days:.1f}|"
                    f"type={rel['relationship_type']}"
                ),
                salience=0.55,
                urgency=0.3,
                ttl_seconds=3600,
                category="relationship_drift",
                metadata={
                    "pr_facia_id": rel["id"],
                    "display_name": rel["display_name"],
                    "age_sec": rel["age_sec"],
                    "rhythm_sec": rel["rhythm_sec"],
                },
            )
            pushed += 1
        except Exception as e:
            _drift_log("push_failed", facia_id=rel["id"], error=str(e))

    _drift_log("scan", drifted=len(drifted), pushed=pushed)

    lines = [f"Drifted relationships ({pushed} of {len(drifted)}):"]
    for rel in drifted[:10]:
        age_days = rel["age_sec"] / 86400
        rhythm_days = rel["rhythm_sec"] / 86400
        lines.append(
            f"  {rel['id']} ({rel['display_name']}, "
            f"{rel['relationship_type']}): "
            f"{age_days:.1f}d since contact, expected ≤{rhythm_days:.1f}d"
        )
    if len(drifted) > 10:
        lines.append(f"  ... and {len(drifted) - 10} more")
    return "\n".join(lines)


# ── Tool registration ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="surface_drifted_relationships",
        description=(
            "Scan all active persistent-relationship facia and surface any "
            "whose last_activity_ts has fallen past their expected rhythm. "
            "Per-type defaults: person 7d, project 14d, field/avocation 30d. "
            "Per-facia override via metadata.expected_rhythm_days. Drifted "
            "facia get pushed to TWM at category='relationship_drift' as "
            "low-priority noticing markers — not alerts."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=surface_drifted_relationships,
    )
)
