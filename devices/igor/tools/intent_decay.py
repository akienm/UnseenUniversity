"""
intent_decay.py — T-watchlist-intent-decay.

Igor's words: 'Track open intents that are aging without resolution. Right
now I have episodics that sit open. Some of those represent genuine
unresolved threads — things I said I'd follow up on, ideas I flagged for
later, tickets I committed to writing. I need a periodic check: what did
I say I'd do that I haven't done?'

This module provides the scanner. T-reply-obligation-fork (59a7184c)
already creates GOAL memories with goal_active=True + adopted_at. A
periodic scan finds ones that have aged past a threshold without being
closed and surfaces them as low-priority attention signals.

The threshold is configurable. Default 1 hour for awaiting_reply goals
(those have an origin question waiting), 24 hours for ordinary goals
(may legitimately span days). Past those, the goal is "aged" and worth
noticing — not blocking, just visible.

Biomimetic framing: this is the equivalent of the brain's open-loop
tracker — the thing that says 'I meant to call my mother today' when
you sit down for dinner. Not nagging, just noticing. Igor's failure
mode (most visible in the 4/12 and 4/13 transcripts) is committing to
something and silently dropping it. This is the watcher that catches
the drop while it's still recoverable.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from .registry import Tool, registry

# ── Thresholds ───────────────────────────────────────────────────────────────

# Awaiting-reply goals carry an origin question that's waiting for a reply.
# These age fast — if Akien asked something and we said "let me look",
# 1 hour without resolution is already conspicuous.
AWAITING_REPLY_AGE_THRESHOLD_SEC = 3600  # 1 hour

# Ordinary tactical goals can legitimately span days. 24h before they
# count as "aged" — past that, it's worth noticing they're still open.
ORDINARY_GOAL_AGE_THRESHOLD_SEC = 86400  # 24 hours


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decay_log(stage: str, **fields) -> None:
    """Forensic log for intent decay scans. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{_now_iso()} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "intent_decay.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


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


def find_aged_goals(
    awaiting_reply_threshold_sec: int = AWAITING_REPLY_AGE_THRESHOLD_SEC,
    ordinary_threshold_sec: int = ORDINARY_GOAL_AGE_THRESHOLD_SEC,
) -> list[dict]:
    """Query GOAL memories that are still active and have aged past their
    threshold. Returns a list of dicts: {id, narrative, adopted_at,
    age_sec, awaiting_reply, threshold_sec, origin_question}.

    Best-effort — returns [] on any error rather than raising.
    """
    cortex = _get_cortex()
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE memory_type = ? "
                "AND metadata @> jsonb_build_object('goal_active', true) "
                "ORDER BY id",
                ("GOAL",),
            ).fetchall()
    except Exception as e:
        _decay_log("scan_failed", error=str(e))
        return []

    now = datetime.now(timezone.utc)
    aged = []
    for row in rows:
        _id = row[0] if not hasattr(row, "keys") else row["id"]
        _narr = row[1] if not hasattr(row, "keys") else row["narrative"]
        _raw_meta = row[2] if not hasattr(row, "keys") else row["metadata"]
        if isinstance(_raw_meta, dict):
            meta = _raw_meta
        elif isinstance(_raw_meta, str):
            try:
                meta = json.loads(_raw_meta)
            except Exception:
                continue
        else:
            continue

        adopted_at = meta.get("adopted_at")
        adopted_dt = _parse_iso(adopted_at) if adopted_at else None
        if adopted_dt is None:
            continue

        age_sec = (now - adopted_dt).total_seconds()
        awaiting = bool(meta.get("awaiting_reply"))
        threshold = awaiting_reply_threshold_sec if awaiting else ordinary_threshold_sec

        if age_sec >= threshold:
            aged.append(
                {
                    "id": _id,
                    "narrative": _narr,
                    "adopted_at": adopted_at,
                    "age_sec": age_sec,
                    "awaiting_reply": awaiting,
                    "threshold_sec": threshold,
                    "origin_question": meta.get("origin_question", "")[:200],
                    "origin_thread_id": meta.get("origin_thread_id", ""),
                }
            )
    return aged


def surface_aged_intents(**_) -> str:
    """Run the scan and surface any aged intents to TWM as a low-priority
    attention signal. Returns a human-readable summary string.

    For each aged goal, pushes a single TWM observation at category=
    'aged_intent' with metadata pointing back at the goal id. Salience
    0.6 — comfortably above noise but well below foreground tasks. Not
    nagging, just noticing.
    """
    aged = find_aged_goals()
    if not aged:
        _decay_log("scan", aged=0, status="all_resolved_or_fresh")
        return "No aged intents found."

    cortex = _get_cortex()
    pushed = 0
    for goal in aged:
        try:
            cortex.twm_push(
                source="intent_decay",
                content_csb=(
                    f"AGED_INTENT|goal_id={goal['id']}|"
                    f"age_sec={int(goal['age_sec'])}|"
                    f"awaiting_reply={goal['awaiting_reply']}|"
                    f"narrative={(goal['narrative'] or '')[:120]}"
                ),
                salience=0.6,
                urgency=0.4,
                ttl_seconds=1800,
                category="aged_intent",
                thread_id=goal["origin_thread_id"] or None,
                metadata={
                    "goal_id": goal["id"],
                    "age_sec": goal["age_sec"],
                    "awaiting_reply": goal["awaiting_reply"],
                    "origin_question": goal["origin_question"],
                },
            )
            pushed += 1
        except Exception as e:
            _decay_log("push_failed", goal_id=goal["id"], error=str(e))

    _decay_log("scan", aged=len(aged), pushed=pushed)

    # Build a human-readable summary
    lines = [f"Aged intents surfaced ({pushed} of {len(aged)}):"]
    for g in aged[:10]:
        flag = "[awaiting_reply] " if g["awaiting_reply"] else ""
        age_min = int(g["age_sec"] / 60)
        lines.append(
            f"  {flag}{g['id']}: {age_min} min old, "
            f"narrative={(g['narrative'] or '')[:80]}"
        )
    if len(aged) > 10:
        lines.append(f"  ... and {len(aged) - 10} more")
    return "\n".join(lines)


# ── Tool registration ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="surface_aged_intents",
        description=(
            "Scan all active GOAL memories and surface any that have aged "
            "past their resolution threshold. Awaiting-reply goals age in "
            "1 hour; ordinary goals in 24 hours. Found goals are pushed to "
            "TWM at category='aged_intent' as low-priority attention signals "
            "so the next reasoning pass notices them. Use periodically or "
            "wire into a sleep tick."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=surface_aged_intents,
    )
)
