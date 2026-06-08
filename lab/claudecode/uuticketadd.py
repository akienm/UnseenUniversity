#!/usr/bin/env python3
"""uuticketadd — append a timestamped note to a ticket's description.

Usage: uuticketadd <ticket-id> <note text...>

Appends:  **Note [YYYY-MM-DD HH:MM]:** <note>
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

PG = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def add_note(ticket_id: str, note: str) -> None:
    try:
        conn = psycopg2.connect(PG)
    except Exception as e:
        print(f"DB unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"\n\n**Note [{ts}]:** {note}"

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
        if not row:
            print(f"Ticket not found: {ticket_id}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        m = row["metadata"]
        m["description"] = (m.get("description") or "") + stamp

        cur.execute(
            """UPDATE clan.memories
               SET metadata = %s::jsonb
               WHERE parent_id = 'TICKETS_ROOT'
                 AND metadata->>'kind' = 'ticket'
                 AND metadata->>'id' = %s""",
            (psycopg2.extras.Json(m), ticket_id),
        )
    conn.commit()
    conn.close()
    print(f"Note added to {ticket_id}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: uuticketadd <ticket-id> <note text...>", file=sys.stderr)
        sys.exit(1)
    add_note(sys.argv[1], " ".join(sys.argv[2:]))
