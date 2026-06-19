#!/usr/bin/env python3
"""ticket_fs_completeness_audit.py — gate for the filesystem-first queue cutover.

Decision D-build-queue-filesystem-first-2026-06-19. cc_queue.py's
``_project_to_memory`` is fail-open: when projection failed the error was
swallowed, so the filesystem ticket store may be SILENTLY MISSING exactly the
tickets where projection broke. Count parity does not prove per-ID parity.

This one-shot audit diffs Postgres ticket IDs against the filesystem store
per-ID, backfills any PG-only ticket into the filesystem, and exits non-zero
if any PG ticket still lacks a filesystem counterpart. It GATES
T-cc-queue-fs-first's flip-to-FS-authoritative and T-ticket-pg-drop.

Postgres read is intentional here — this is the migration bridge, the one
place allowed to read clan.memories/devlab.tickets during cutover.

Usage:
    python3 ticket_fs_completeness_audit.py            # report + backfill + gate
    python3 ticket_fs_completeness_audit.py --no-backfill   # report only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# memory_emit is the canonical filesystem chokepoint (one writer).
from memory_emit import MEMORY_ROOT, emit

log = logging.getLogger("ticket_fs_audit")

TICKETS_ROOT_ID = "TICKETS_ROOT"
TICKETS_DIR = Path(MEMORY_ROOT) / "tickets"
CLOSED_DIR = TICKETS_DIR / "closed"


# ── Postgres side (migration bridge — the only allowed ticket-state PG read) ──


def _db_conn():
    import psycopg2

    url = os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_HOME_DB_URL")
    if not url:
        raise RuntimeError("UU_HOME_DB_URL / IGOR_HOME_DB_URL not set")
    return psycopg2.connect(url, connect_timeout=10)


def _pg_tickets() -> dict[str, dict]:
    """Return {ticket_id: ticket_body} merged from clan.memories + devlab.tickets.

    Mirrors cc_queue._load() ID semantics: clan.memories metadata (parent_id=
    TICKETS_ROOT) plus devlab.tickets rows, devlab preferred on conflict.
    """
    conn = _db_conn()
    tickets: dict[str, dict] = {}
    try:
        cur = conn.cursor()

        # clan.memories — existing tickets (metadata JSONB carries the id)
        cur.execute(
            "SELECT metadata FROM clan.memories WHERE parent_id = %s",
            (TICKETS_ROOT_ID,),
        )
        for (md,) in cur.fetchall():
            if not md:
                continue
            t = dict(md)
            t.pop("kind", None)
            tid = t.get("id")
            if tid:
                tickets[tid] = t

        # devlab.tickets — newer tickets (explicit columns), override clan
        cur.execute(
            """SELECT id, title, status, worker, size, tags, description,
                      decision_id, metadata, created_at, updated_at, completed_at
               FROM devlab.tickets"""
        )
        for row in cur.fetchall():
            (tid, title, status, worker, size, tags, description,
             decision_id, metadata, created_at, updated_at, completed_at) = row
            t = {
                "id": tid, "title": title, "status": status, "worker": worker,
                "size": size, "tags": tags or [], "description": description,
                "decision_id": decision_id,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "completed_at": completed_at.isoformat() if completed_at else None,
            }
            if metadata:
                t.update(metadata)
            if tid:
                tickets[tid] = t

        return tickets
    finally:
        conn.close()


# ── Filesystem side ───────────────────────────────────────────────────────────


def _fs_ticket_ids() -> set[str]:
    """Logical ticket IDs present in the filesystem store (active + closed)."""
    ids: set[str] = set()
    for d in (TICKETS_DIR, CLOSED_DIR):
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                rec = json.loads(p.read_text())
            except Exception as exc:
                log.warning("unreadable ticket file %s: %s", p.name, exc)
                continue
            # logical id lives in body.id; fall back to top-level if shaped flat
            body = rec.get("body") if isinstance(rec, dict) else None
            tid = (body or {}).get("id") if isinstance(body, dict) else None
            if not tid and isinstance(rec, dict):
                tid = rec.get("id")
            if tid:
                ids.add(tid)
    return ids


def _backfill(tid: str, body: dict) -> str:
    """Write a PG-only ticket into the filesystem store via the canonical emit."""
    links = {"tickets": [tid]}
    if body.get("decision_id"):
        links["decisions"] = [body["decision_id"]]
    return emit("tickets", body.get("created_by") or "cc.0", body,
                kind="ticket", namespace=[tid], links=links)


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-backfill", action="store_true",
                    help="report only; do not write missing tickets to the filesystem")
    args = ap.parse_args()

    try:
        pg = _pg_tickets()
    except Exception as exc:
        log.error("Postgres read failed (%s) — cannot audit completeness.", exc)
        return 2

    fs_ids = _fs_ticket_ids()
    pg_ids = set(pg)

    pg_only = sorted(pg_ids - fs_ids)   # silent-drop risk — must be 0 to pass
    fs_only = sorted(fs_ids - pg_ids)   # newer / filesystem-native — expected

    log.info("ticket completeness: PG=%d  FS=%d  PG-only=%d  FS-only=%d",
             len(pg_ids), len(fs_ids), len(pg_only), len(fs_only))
    if fs_only:
        log.info("  FS-only (expected — newer than PG): %d e.g. %s",
                 len(fs_only), ", ".join(fs_only[:5]))
    if pg_only:
        log.info("  PG-only (MISSING from filesystem): %s", ", ".join(pg_only))

    if pg_only and not args.no_backfill:
        log.info("backfilling %d PG-only ticket(s) into the filesystem store...",
                 len(pg_only))
        backfilled = 0
        for tid in pg_only:
            try:
                path = _backfill(tid, pg[tid])
                backfilled += 1
                log.info("  backfilled %s -> %s", tid, path)
            except Exception as exc:
                log.error("  FAILED to backfill %s: %s", tid, exc)
        # re-diff after backfill
        fs_ids = _fs_ticket_ids()
        pg_only = sorted(pg_ids - fs_ids)
        log.info("after backfill: %d backfilled, PG-only remaining=%d",
                 backfilled, len(pg_only))

    if pg_only:
        log.error("INCOMPLETE: %d PG ticket(s) lack a filesystem counterpart: %s",
                  len(pg_only), ", ".join(pg_only))
        return 1

    log.info("COMPLETE: every Postgres ticket has a filesystem counterpart. "
             "Cutover gate PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
