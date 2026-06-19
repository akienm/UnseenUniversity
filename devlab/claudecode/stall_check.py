#!/usr/bin/env python3
"""
stall_check.py — Detect stalled in_progress tickets.

A ticket is stalled when its status is in_progress AND its age (measured from
dispatched_at, or updated_at as fallback) exceeds the threshold (default 2h).

Usage:
    python3 stall_check.py                    # print stalled tickets, exit 1 if any
    python3 stall_check.py --threshold 4      # raise threshold to 4 hours
    python3 stall_check.py --all              # list all in_progress tickets, not just stalled

Output (one line per stalled ticket):
    [STALL?] T-xxx (5.2h) — ticket title

Exit codes:
    0 — no stalls (or DB unavailable — fail-open, never blocks context-load)
    1 — at least one stall detected

Called by /stall-check skill and by context-load Step 5.5.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone


_DEFAULT_THRESHOLD_HOURS = 2.0


# ── Pure functions (no I/O — fully unit-testable) ─────────────────────────────


def _parse_ts(s: str | None) -> datetime | None:
    """Parse an ISO timestamp string → UTC-aware datetime, or None on failure."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def compute_stall_info(
    ticket: dict,
    now: datetime,
    threshold_hours: float = _DEFAULT_THRESHOLD_HOURS,
) -> dict | None:
    """Return stall info for a ticket, or None if not stalled.

    A ticket is stalled when:
    - status == 'in_progress'
    - age (from dispatched_at, or updated_at as fallback) > threshold_hours

    Returns None when: not in_progress, no timing info, or age within threshold.
    """
    if ticket.get("status") != "in_progress":
        return None

    ts = _parse_ts(ticket.get("dispatched_at")) or _parse_ts(ticket.get("updated_at"))
    if ts is None:
        return None

    age_hours = (now - ts).total_seconds() / 3600
    if age_hours <= threshold_hours:
        return None

    title = ticket.get("title") or "?"
    # Strip status prefix from title if present (e.g. "[in_progress] foo" → "foo")
    for prefix in ("[in_progress] ", "[sprint] ", "[hold] "):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break

    return {
        "id": ticket.get("id", "?"),
        "title": title,
        "age_hours": age_hours,
        "dispatched_at": ticket.get("dispatched_at") or ticket.get("updated_at"),
        "worker": ticket.get("worker", "?"),
    }


def find_stalls(
    tasks: list[dict],
    now: datetime,
    threshold_hours: float = _DEFAULT_THRESHOLD_HOURS,
) -> list[dict]:
    """Return stall info dicts for all stalled tickets, sorted oldest-first."""
    stalls = []
    for t in tasks:
        info = compute_stall_info(t, now, threshold_hours)
        if info:
            stalls.append(info)
    return sorted(stalls, key=lambda x: x["age_hours"], reverse=True)


# ── DB I/O ────────────────────────────────────────────────────────────────────


def _load_in_progress_tickets() -> list[dict]:
    """Return in_progress tickets from the filesystem ticket store. [] on error.

    Staleness math keys on ``dispatched_at``/``updated_at`` via ``_parse_ts``,
    which already normalizes naive timestamps to UTC — so FS bodies (same field
    shape the PG metadata carried) need no extra TZ handling here.
    """
    try:
        from unseen_university import ticket_store

        return list(ticket_store.list(status_filter="in_progress"))
    except Exception as exc:
        print(f"[stall-check] ticket_store error: {exc}", file=sys.stderr)
        return []


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List stalled in_progress tickets",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD_HOURS,
        metavar="HOURS",
        help="Age threshold in hours (default: %(default)s)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="List all in_progress tickets regardless of age",
    )
    args = parser.parse_args(argv)

    tasks = _load_in_progress_tickets()
    now = datetime.now(timezone.utc)

    if args.show_all:
        # List every in_progress ticket with its age
        in_progress = []
        for t in tasks:
            ts = _parse_ts(t.get("dispatched_at")) or _parse_ts(t.get("updated_at"))
            age_str = f"{(now - ts).total_seconds() / 3600:.1f}h" if ts else "age:?"
            title = t.get("title") or "?"
            for prefix in ("[in_progress] ", "[sprint] ", "[hold] "):
                if title.startswith(prefix):
                    title = title[len(prefix):]
                    break
            in_progress.append((age_str, t.get("id", "?"), title))
        if not in_progress:
            print("no in_progress tickets")
        else:
            for age_str, tid, title in sorted(
                in_progress, key=lambda x: float(x[0].rstrip("h").replace("age:?", "0")), reverse=True
            ):
                print(f"  {tid} ({age_str}) — {title}")
        return 0

    stalls = find_stalls(tasks, now, args.threshold)
    if not stalls:
        return 0

    for s in stalls:
        print(f"[STALL?] {s['id']} ({s['age_hours']:.1f}h) — {s['title']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
