#!/usr/bin/env python3
"""uushowticket — pretty-print a ticket for human reading.

Usage: uushowticket <ticket-id>
"""
from __future__ import annotations

import sys

from unseen_university import ticket_store

# D-ticket-status-model-2026-06-16: design / open_questions fold into triage.
STATUS_ICON = {
    "in_progress": "🔵",
    "sprint":      "⬜",
    "triage":      "🔍",
    "hold":        "🔒",
    "dependency":  "🔗",
    "done":        "✅",
    "closed":      "✅",
    "cancelled":   "❌",
    # legacy (folded → triage):
    "design":      "🔍",
    "open_questions": "🔍",
    "needs_review": "🔍",
    "akien":       "👤",
}


def show(ticket_id: str) -> None:
    t = ticket_store.read(ticket_id)
    if not t:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        sys.exit(1)

    status = t.get("status", "?")
    icon = STATUS_ICON.get(status, "·")
    title = t.get("title", "")
    if title.startswith("[") and "]" in title:
        title = title[title.index("]") + 1:].strip()

    tags = ", ".join(t.get("tags") or [])
    gate = t.get("gate") or ""

    print(f"{icon} {ticket_id}  ({t.get('size','?')})  [{status}]")
    print(f"  Title:    {title}")
    if tags:
        print(f"  Tags:     {tags}")
    print(f"  Priority: {t.get('priority','?')}   Worker: {t.get('worker','?')}   Role: {t.get('role','?')}")
    if gate:
        print(f"  Gate:     {gate}")
    intention = t.get("intention") or ""
    if intention:
        print(f"  Intention: {intention}")
    print()
    desc = t.get("description") or ""
    if desc:
        print(desc)
    result = t.get("result") or ""
    if result:
        print(f"\n── Result ──\n{result}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: uushowticket <ticket-id>", file=sys.stderr)
        sys.exit(1)
    show(sys.argv[1])
