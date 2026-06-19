#!/usr/bin/env python3
"""uuticketadd — append a timestamped note to a ticket's description.

Usage: uuticketadd <ticket-id> <note text...>

Appends:  **Note [YYYY-MM-DD HH:MM]:** <note>
"""
from __future__ import annotations

import sys
from datetime import datetime


def add_note(ticket_id: str, note: str) -> None:
    """Append a timestamped note to a ticket's description in the filesystem store.

    A read-modify-write — routed through ``ticket_store.conditional_update`` so the
    append happens atomically under the store's mutation lock (no read-then-write
    TOCTOU vs a concurrent status transition). The status precondition is incidental
    here (a note isn't a status change), so we pass the ticket's current status and
    retry if it shifted between the pre-read and the locked write.
    """
    from unseen_university import ticket_store

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"\n\n**Note [{ts}]:** {note}"

    def _append(body: dict) -> dict:
        body["description"] = (body.get("description") or "") + stamp
        return body

    for _ in range(5):
        body = ticket_store.read(ticket_id)
        if body is None:
            print(f"Ticket not found: {ticket_id}", file=sys.stderr)
            sys.exit(1)
        try:
            path = ticket_store.conditional_update(
                ticket_id, expect_current=body.get("status"), mutate=_append
            )
        except KeyError:
            print(f"Ticket not found: {ticket_id}", file=sys.stderr)
            sys.exit(1)
        if path is not None:
            print(f"Note added to {ticket_id}")
            return

    print(f"Note add failed (status kept changing): {ticket_id}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: uuticketadd <ticket-id> <note text...>", file=sys.stderr)
        sys.exit(1)
    add_note(sys.argv[1], " ".join(sys.argv[2:]))
