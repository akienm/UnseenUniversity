#!/usr/bin/env python3
"""uu_tickets — open tickets from the filesystem store, salience-ordered.

Backs two `uu` verbs: `uu opentickets` (all open) and `uu mytickets` (--mine —
Akien's). Reads the CANONICAL filesystem ticket store via
``unseen_university.ticket_store`` — the Postgres DB is NO LONGER the source of
truth for tickets or any build artifact; they live as grep-able JSON under
``devlab/runtime/memory/tickets/``. Display status and ordering come from
``unseen_university.ticket_status`` (``effective_status`` + ``STATUS_ORDER``),
the single taxonomy source, so this view can never drift from the web/queue
renderers. Zero inference, no CC process — runs in a bare shell.

Reads ``ticket_store.list()`` directly (the complete reader ``show`` uses), not
``cc_queue.py list`` — that command is known to hide tickets
(T-cc-queue-list-hides-tickets); the store-level read does not.
"""
from __future__ import annotations

import sys

from unseen_university import ticket_store
from unseen_university.ticket_status import (
    STATUS_ORDER,
    effective_status,
    status_label,
)

# Terminal statuses never shown as "open" (defensive — closed tickets normally
# move to the closed/ dir, but a stale done/cancelled in the open dir is filtered
# here for faithfulness to the prior uuopentickets behaviour).
_CLOSED = {"done", "closed", "cancelled"}

# Akien's at-a-glance "mine" filter: tickets he owns or that need his action.
_WORKER_MARKER = {"igor": " [⚠ igor]", "akien": " [👤]"}


def _is_akiens(t: dict) -> bool:
    return (
        t.get("worker") == "akien"
        or t.get("role") == "guru"
        or t.get("status") == "akien"
    )


def _display_title(t: dict) -> str:
    """Strip any legacy ``[status]`` prefix the title may still carry."""
    title = t.get("title", "") or ""
    if title.startswith("[") and "]" in title:
        title = title[title.index("]") + 1:].strip()
    return title


def main(argv: list[str]) -> int:
    mine = "--mine" in argv[1:]

    tickets = [t for t in ticket_store.list() if t.get("status") not in _CLOSED]
    if mine:
        tickets = [t for t in tickets if _is_akiens(t)]

    if not tickets:
        print("No tickets assigned to Akien right now." if mine else "No open tickets.")
        return 0

    by_status: dict[str, list] = {}
    for t in tickets:
        by_status.setdefault(effective_status(t, tickets), []).append(t)

    # Salience order first, then any unrecognised status appended (sorted) so a
    # ticket is never silently dropped from the view.
    order = [s for s in STATUS_ORDER if s in by_status]
    order += sorted(s for s in by_status if s not in STATUS_ORDER)

    print(f"{'MY TICKETS — Akien' if mine else 'OPEN TICKETS'} ({len(tickets)} open)")
    totals = []
    for status in order:
        group = sorted(
            by_status[status],
            key=lambda x: (-(x.get("priority") or 0.0), x.get("id", "")),
        )
        print(f"\n{status_label(status)} ({len(group)}):")
        for t in group:
            tid = t.get("id", "?")
            size = t.get("size", "?")
            wmark = _WORKER_MARKER.get(t.get("worker", "") or "", "")
            gate = t.get("gate", "") or ""
            gate_str = f" [gate: {gate}]" if gate else ""
            print(f"  {tid:<45s} ({size}){wmark}{gate_str}  {_display_title(t)}")
        totals.append(f"{len(group)} {status}")

    print(f"\nTotals: {' · '.join(totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
