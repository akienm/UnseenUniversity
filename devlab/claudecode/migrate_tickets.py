#!/usr/bin/env python3
"""Migrator: cc_queue tickets (clan.memories + devlab.tickets)  ->  memory store `tickets/`.

Projects every ticket cc_queue can see (its canonical MERGED read across the two
transition tables) into the filesystem memory store. Same locked policy as
migrate_decisions.py:

  - PROJECTION not relocation: reads cc_queue.load_tasks(), writes a projection,
    deletes nothing. The DB tables stay authoritative this pass.
  - IDEMPOTENT: the ticket id is carried in the namespace, so the filename is
    `cc.0.<ticket-id>.<stamp>.json` — unique per ticket no matter what. The stamp
    is derived from the ticket's ORIGINAL created_at (stable in the DB), so a
    re-run overwrites in place. Tickets with no timestamp at all fall back to
    stamp_for_day_only(ticket_id, ...) — deterministic from the semantic id.
  - SEMANTIC-ID LINKS: decision_id + any T-/D- tokens in related_to/description/
    gate are parsed into links as T-…/D-… semantic ids, never filenames.
  - PROVENANCE: body.source records the projection came from cc_queue's merged read.

Run:  python3 devlab/claudecode/migrate_tickets.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime

import cc_queue
from memory_emit import emit, stamp_from_dt, stamp_for_day_only

_TICKET_TOK = re.compile(r"\bT-[a-z0-9][a-z0-9-]+", re.I)
_DECISION_TOK = re.compile(r"\b[CD]-[a-z0-9][a-z0-9-]+", re.I)

# Last-resort deterministic date for the rare ticket with no timestamp at all.
# stamp_for_day_only hashes the (unique) ticket id into the clock field, so even
# a shared sentinel date yields a stable, distinct stamp per ticket.
_NO_DATE_SENTINEL = "20260101"


def _stamp_for(t: dict) -> str:
    """Stable stamp from the ticket's original time; deterministic fallback."""
    for field in ("created_at", "updated_at", "completed_at"):
        raw = t.get(field)
        if raw:
            try:
                return stamp_from_dt(datetime.fromisoformat(raw))
            except (ValueError, TypeError):
                continue
    return stamp_for_day_only(t.get("id", "T-unknown"), _NO_DATE_SENTINEL)


def links_for(t: dict) -> dict:
    """decision_id + related_to + gate + description T-/D- tokens -> semantic links.
    The ticket's own id is the anchor in tickets[]."""
    tid = t.get("id", "")
    blob = " ".join(
        str(t.get(k, "") or "")
        for k in ("related_to", "gate", "description", "decision_id")
    )
    tickets = sorted(set(_TICKET_TOK.findall(blob)) | ({tid} if tid else set()))
    decisions = sorted(set(_DECISION_TOK.findall(blob)))
    if t.get("decision_id"):
        decisions = sorted(set(decisions) | {t["decision_id"]})
    return {"tickets": tickets, "decisions": decisions}


def migrate_one(t: dict, dry_run: bool = False) -> str:
    tid = t.get("id") or "T-unknown"
    stamp = _stamp_for(t)
    body = dict(t)  # the whole ticket dict is the body — plain readable strings
    body["source"] = "cc_queue.load_tasks() (clan.memories + devlab.tickets merged read)"
    if dry_run:
        return f"[dry] tickets/cc.0.{tid}.{stamp}.json  ({t.get('status','?')})"
    return emit("tickets", "cc.0", body, kind="ticket",
                namespace=[tid], links=links_for(t), stamp=stamp)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tasks = cc_queue.load_tasks()
    out = [migrate_one(t, args.dry_run) for t in tasks]
    print(f"{'(dry) ' if args.dry_run else ''}migrated {len(out)} tickets")
    for line in out[:5]:
        print("  e.g.", line)


if __name__ == "__main__":
    main()
