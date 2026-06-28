"""
build_digester.log_parser — Extract ticket-keyed events from JSONL log lines.

Parses events from two sources:
  1. ~/.unseen_university/cc_channel/log.jsonl  (ticket lifecycle events)
  2. datacenter_logs/queue/trace/<date>.jsonl   (queue_next events)

Returns structured event dicts suitable for upsert into devlab.build_digest.

Gracefully degrades to a flat timeline when structured boundary markers
(attempt_start / attempt_end) are absent from the logs.
"""

from __future__ import annotations

import json
from typing import Optional

# Actions that indicate attempt/outcome boundaries if they ever appear.
_BOUNDARY_ACTIONS = {"attempt_start", "attempt_end", "build_event"}

# Actions that carry a ticket_id we care about.
_TICKET_ACTIONS = {
    "add",
    "setstatus",
    "close",
    "awaiting_validation",
    "hold",
    "dispatch",
    "claim",
    "note",
    "gate_tripped",
    "ungate_on_close",
    "queue_next",
    "attempt_start",
    "attempt_end",
    "build_event",
}


def _extract_ticket_id(entry: dict) -> Optional[str]:
    """Return the ticket_id from an entry, checking common key names."""
    for key in ("id", "ticket_id", "closed_id"):
        val = entry.get(key)
        if val and isinstance(val, str) and val.startswith("T-"):
            return val
    return None


def _summarize(entry: dict, action: str) -> str:
    """Build a short human-readable summary for an event."""
    if action == "add":
        return f"filed: {entry.get('title', '')[:80]}"
    if action == "setstatus":
        return f"{entry.get('old', '?')} → {entry.get('new', '?')}"
    if action == "close":
        result = entry.get("result", "")
        return f"closed: {result[:100]}" if result else "closed"
    if action == "awaiting_validation":
        result = entry.get("result", "")
        return f"awaiting_validation: {result[:80]}" if result else "awaiting_validation"
    if action == "hold":
        return f"hold: {entry.get('reason', '')[:80]}"
    if action == "dispatch":
        return f"dispatched by {entry.get('dispatched_by', '?')}"
    if action == "queue_next":
        return f"claimed by {entry.get('data', {}).get('worker', entry.get('worker', '?'))}"
    if action in ("attempt_start", "attempt_end", "build_event"):
        return f"{action}: {entry.get('detail', '')[:80]}"
    if action == "gate_tripped":
        return f"gate_tripped: {entry.get('reason', '')[:60]}"
    if action == "ungate_on_close":
        n = entry.get("ungated_count", 0)
        return f"ungate_on_close ({n} ungated)"
    if action == "note":
        return f"note: {entry.get('message', '')[:80]}"
    return action


def parse_line(line: str) -> Optional[dict]:
    """Parse one JSONL line into an event dict, or return None.

    None is returned for:
    - malformed JSON
    - entries with no ticket_id
    - entries with an action not in _TICKET_ACTIONS

    Returned dict shape::

        {
            "ticket_id": str,
            "ts": str,           # ISO timestamp from entry, or ""
            "action": str,
            "summary": str,
            "has_boundary_marker": bool,  # True only for boundary actions
        }
    """
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    # queue_next events nest ticket_id under "data"
    action = entry.get("action") or entry.get("event")
    if not action:
        return None
    if action not in _TICKET_ACTIONS:
        return None

    ticket_id = _extract_ticket_id(entry)
    if not ticket_id:
        # queue_next from queue trace has ticket_id under data
        if action == "queue_next" and isinstance(entry.get("data"), dict):
            ticket_id = entry["data"].get("ticket_id")
        if not ticket_id:
            return None

    return {
        "ticket_id": ticket_id,
        "ts": entry.get("ts", ""),
        "action": action,
        "summary": _summarize(entry, action),
        "has_boundary_marker": action in _BOUNDARY_ACTIONS,
    }


def parse_log_file(path: str, start_offset: int = 0) -> tuple[list[dict], int]:
    """Parse a JSONL log file starting at byte offset *start_offset*.

    Returns (events, new_offset) where new_offset is the file position after
    the last fully-read line — suitable for persisting as a cursor.

    Events are returned in file order (oldest first).
    """
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            fh.seek(start_offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                evt = parse_line(line)
                if evt:
                    events.append(evt)
            new_offset = fh.tell()
    except (OSError, IOError):
        return [], start_offset
    return events, new_offset
