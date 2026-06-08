#!/usr/bin/env python3
"""uushowticket — pretty-print a ticket for human reading.

Usage: uushowticket <ticket-id>
"""
from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extras

PG = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

STATUS_ICON = {
    "in_progress": "🔵",
    "sprint":      "⬜",
    "design":      "📐",
    "open_questions": "❓",
    "hold":        "🔒",
    "triage":      "🔍",
    "done":        "✅",
    "closed":      "✅",
    "cancelled":   "❌",
    "akien":       "👤",
}


def show(ticket_id: str) -> None:
    try:
        conn = psycopg2.connect(PG)
    except Exception as e:
        print(f"DB unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT metadata FROM clan.memories
               WHERE parent_id = 'TICKETS_ROOT'
                 AND metadata->>'kind' = 'ticket'
                 AND metadata->>'id' = %s
               LIMIT 1""",
            (ticket_id,),
        )
        row = cur.fetchone()
    conn.close()

    if not row:
        print(f"Ticket not found: {ticket_id}", file=sys.stderr)
        sys.exit(1)

    t = row["metadata"]
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
