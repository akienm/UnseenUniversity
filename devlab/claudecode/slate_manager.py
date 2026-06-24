#!/usr/bin/env python3
"""
slate_manager.py — Manage the active daily slate in Postgres.

D304: Slates are daily files at <repo>/devlab/runtime/memory/slates/YYYYMMDD.slate.txt
(resolved via unseen_university.slate_store; T-slate-location-canonical-devlab).
Epics are category tags on tickets (not slate sections).

A slate (DB) represents one day's work bundle:
  - position: 0=today (the only active one rendered to file)
  - tickets: [{id, title, type: primary|adopted_bug, status}]

Usage:
    python3 claudecode/slate_manager.py show          — print current slate from DB
    python3 claudecode/slate_manager.py render        — write YYYYMMDD.slate.txt from DB
    python3 claudecode/slate_manager.py seed          — seed DB (first run only)
    python3 claudecode/slate_manager.py add-ticket <slate_pos> <ticket_id> <title> [--bug]
    python3 claudecode/slate_manager.py close-ticket <ticket_id>
    python3 claudecode/slate_manager.py advance       — close today's slate, shift remaining

DB: UU_HOME_DB_URL (Postgres). Falls back to printing only if not set.

Ref: D130, D132, D304
"""

import json
from unseen_university._uu_root import uu_home
import os
import sys
from datetime import datetime
from pathlib import Path

from unseen_university import slate_store

DB_URL = os.getenv("UU_HOME_DB_URL") or os.getenv("IGOR_DB_URL")

_IGOR_HOME = Path(uu_home())


def _today_slate_path() -> Path:
    return slate_store.today_slate_path()


# ── DB helpers ────────────────────────────────────────────────────────────────


def _conn():
    import psycopg2
    import psycopg2.extras

    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _ensure_table():
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS slates (
                    id         TEXT PRIMARY KEY,
                    position   INTEGER NOT NULL,
                    name       TEXT NOT NULL,
                    done_when  TEXT,
                    tickets    JSONB DEFAULT '[]',
                    notes      TEXT,
                    status     TEXT DEFAULT 'active',
                    created_at TEXT,
                    closed_at  TEXT
                )
            """)
            c.execute(
                """
                INSERT INTO _migrations (name, applied_at)
                VALUES ('slates_table', %s)
                ON CONFLICT (name) DO NOTHING
            """,
                (datetime.now().isoformat(),),
            )
        conn.commit()


def _load_slates() -> list:
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM slates WHERE status='active' ORDER BY position")
            return [dict(r) for r in c.fetchall()]


def _upsert_slate(s: dict):
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO slates (id, position, name, done_when, tickets, notes, status, created_at)
                VALUES (%(id)s, %(position)s, %(name)s, %(done_when)s,
                        %(tickets)s::jsonb, %(notes)s, %(status)s, %(created_at)s)
                ON CONFLICT (id) DO UPDATE SET
                    position  = EXCLUDED.position,
                    name      = EXCLUDED.name,
                    done_when = EXCLUDED.done_when,
                    tickets   = EXCLUDED.tickets,
                    notes     = EXCLUDED.notes,
                    status    = EXCLUDED.status
            """,
                {**s, "tickets": json.dumps(s.get("tickets", []))},
            )
        conn.commit()


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_seed():
    """Seed DB with current slate 0–3 data."""
    _ensure_table()

    now = datetime.now().strftime("%Y-%m-%dT%H:%M")

    slates = [
        {
            "id": "slate-20260319-0",
            "position": 0,
            "name": "Workflow + CC web server",
            "done_when": "context-load → skills → process loop all work end-to-end; all running CC instances connect to Igor web UI.",
            "tickets": [
                {
                    "id": "T-slate-schema",
                    "title": "Define slate as DB node",
                    "type": "primary",
                    "status": "in_progress",
                },
                {
                    "id": "T-channel-extract",
                    "title": "Extract web channel to standalone process",
                    "type": "primary",
                    "status": "pending",
                },
                {
                    "id": "T-organizer-github-sync",
                    "title": "Organizer step 0 GitHub→DB sync",
                    "type": "primary",
                    "status": "pending",
                },
                {
                    "id": "T-docs-tree-in-db",
                    "title": "Mirror DSB/CSB docs as Postgres nodes",
                    "type": "primary",
                    "status": "pending",
                },
                {
                    "id": "T-context-load-skill",
                    "title": "context-load skill",
                    "type": "primary",
                    "status": "done",
                },
                {
                    "id": "T-sprint-skill",
                    "title": "sprint skill",
                    "type": "primary",
                    "status": "done",
                },
                {
                    "id": "T-cc-webui-multiinstance",
                    "title": "CC instances connect to Igor web UI",
                    "type": "primary",
                    "status": "done",
                },
                {
                    "id": "bug-budget-config",
                    "title": "budget.py config table missing in Postgres",
                    "type": "adopted_bug",
                    "status": "done",
                },
                {
                    "id": "bug-https-misread",
                    "title": "web server HTTPS misread as broken",
                    "type": "adopted_bug",
                    "status": "done",
                },
            ],
            "notes": None,
            "status": "active",
            "created_at": "2026-03-19T06:00",
        },
        {
            "id": "slate-20260319-1",
            "position": 1,
            "name": "DB optimization",
            "done_when": None,
            "tickets": [
                {
                    "id": "T-slow-query-analysis",
                    "title": "Slow query analysis tool + habit",
                    "type": "primary",
                    "status": "pending",
                },
                {
                    "id": "T-habit-audit-pipeline",
                    "title": "Audit + rewrite early habits",
                    "type": "primary",
                    "status": "pending",
                },
            ],
            "notes": "Slow query analysis, index tuning, query patterns from db_queries.log.",
            "status": "active",
            "created_at": now,
        },
        {
            "id": "slate-20260319-2",
            "position": 2,
            "name": "Training + neural process engineering",
            "done_when": None,
            "tickets": [
                {
                    "id": "T-trails-infra",
                    "title": "Trail infrastructure — first-class activation trail primitive",
                    "type": "primary",
                    "status": "pending",
                },
            ],
            "notes": "Trails infrastructure, wg_cooccur replacement, temporal gradient consolidation.",
            "status": "active",
            "created_at": now,
        },
        {
            "id": "slate-20260319-3",
            "position": 3,
            "name": "Productization",
            "done_when": None,
            "tickets": [],
            "notes": "Windows round, multi-box reading, public-facing story. Vague — will sharpen as we approach.",
            "status": "active",
            "created_at": now,
        },
    ]

    for s in slates:
        _upsert_slate(s)
        print(f"  seeded: slate-{s['position']} — {s['name']}")

    print("Seed complete.")


def cmd_show():
    """Print current slates from DB."""
    slates = _load_slates()
    for s in slates:
        label = ["TODAY", "NEXT", "AFTER NEXT", "FUTURE"][min(s["position"], 3)]
        print(f"\nSlate {s['position']} — {s['name']}  [{label}]")
        if s.get("done_when"):
            print(f"  Done when: {s['done_when']}")
        if s.get("notes"):
            print(f"  Shape: {s['notes']}")
        tickets = s.get("tickets") or []
        primary = [t for t in tickets if t.get("type") != "adopted_bug"]
        bugs = [t for t in tickets if t.get("type") == "adopted_bug"]
        for t in primary:
            mark = "✓" if t.get("status") == "done" else "·"
            print(f"    {mark} {t['id']}: {t['title']}")
        for t in bugs:
            mark = "✓" if t.get("status") == "done" else "·"
            print(f"    {mark} [bug] {t['id']}: {t['title']}")


def _queue_done_ids() -> set:
    """Return set of ticket IDs marked done in cc_queue (cross-reference for render)."""
    try:
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from cc_queue import load_tasks

        tasks = load_tasks()
        return {t["id"] for t in tasks if t.get("status") == "done"}
    except Exception:
        return set()


def cmd_render():
    """Write today's dated slate file from DB (D304: slates are daily files)."""
    slates = _load_slates()
    today_slate = next((s for s in slates if s["position"] == 0), None)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = _today_slate_path()
    slate_store.slates_dir().mkdir(parents=True, exist_ok=True)

    # Cross-reference with queue: tickets done in queue are done on slate too
    queue_done = _queue_done_ids()

    lines = [f"# Slate {date_str}", ""]

    if today_slate:
        tickets = today_slate.get("tickets") or []
        # A ticket is done if slate says so OR queue says so
        for t in tickets:
            if t["id"] in queue_done:
                t["status"] = "done"
        open_tickets = [t for t in tickets if t.get("status") != "done"]
        done_tickets = [t for t in tickets if t.get("status") == "done"]

        if open_tickets:
            lines.append("## Active")
            for t in open_tickets:
                lines.append(f"- {t['id']}: {t['title']}")
            lines.append("")

        if done_tickets:
            lines.append("## Done today")
            for t in done_tickets:
                lines.append(f"- ~~{t['id']}~~ ✓  {t['title']}")
            lines.append("")

    lines += [
        "## Tools",
        "Skills: /sprint /deep-audit /decided /commit /savestate /fixit /context-load /day-close /day-close-audit /probe /notethat /slateclose /readigor",
        "MCP: mcp__igor__memory_get(id) · mcp__igor__cc_send(text) · mcp__igor__channel_read(limit=N)",
        "DB: psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        "Design decisions: devlab/runtime/memory/decisions/ or mcp__igor__memory_get('D304')",
        "Epics: Claude · Cognition · Training · Operations · Database · Swarm · Productization",
        "",
        "## Design thread",
        "D304: Slates = daily files at ~/.unseen_university/claudecode/YYYYMMDD.slate.txt. Epics = category tags on tickets.",
        "D305: Context load: dated slate + decisions top 30 + channel last 5 + session last-change + Tools block.",
        "D130: DB is source of truth; GitHub/files are sync targets.",
        "D131: DSB/CSB docs mirrored as Postgres nodes — token-efficient context load.",
    ]

    out_path.write_text("\n".join(lines) + "\n")
    print(f"Rendered → {out_path}")


def cmd_add_ticket(pos: int, ticket_id: str, title: str, is_bug: bool = False):
    """Add a ticket to a slate by position."""
    slates = _load_slates()
    target = next((s for s in slates if s["position"] == pos), None)
    if not target:
        print(f"No active slate at position {pos}", file=sys.stderr)
        sys.exit(1)
    tickets = target.get("tickets") or []
    if any(t["id"] == ticket_id for t in tickets):
        print(f"{ticket_id} already in slate")
        return
    tickets.append(
        {
            "id": ticket_id,
            "title": title,
            "type": "adopted_bug" if is_bug else "primary",
            "status": "pending",
        }
    )
    target["tickets"] = tickets
    _upsert_slate(target)
    print(f"Added {ticket_id} to slate {pos}")


def cmd_close_ticket(ticket_id: str):
    """Mark a ticket done across all slates."""
    slates = _load_slates()
    for s in slates:
        tickets = s.get("tickets") or []
        changed = False
        for t in tickets:
            if t["id"] == ticket_id and t.get("status") != "done":
                t["status"] = "done"
                changed = True
        if changed:
            s["tickets"] = tickets
            _upsert_slate(s)
            print(f"Closed {ticket_id} in slate {s['position']}")


def cmd_advance():
    """Close slate 0 and shift remaining slates down by 1."""
    slates = _load_slates()
    slate0 = next((s for s in slates if s["position"] == 0), None)
    if not slate0:
        print("No active slate 0", file=sys.stderr)
        sys.exit(1)
    now = datetime.now().isoformat()
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE slates SET status='done', closed_at=%s WHERE id=%s",
                (now, slate0["id"]),
            )
            for s in slates:
                if s["position"] > 0:
                    c.execute(
                        "UPDATE slates SET position=%s WHERE id=%s",
                        (s["position"] - 1, s["id"]),
                    )
        conn.commit()
    print(f"Closed: {slate0['name']}")
    print("Remaining slates shifted down.")


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    if not DB_URL:
        print("ERROR: UU_HOME_DB_URL not set", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    if cmd == "seed":
        cmd_seed()
    elif cmd == "show":
        cmd_show()
    elif cmd == "render":
        cmd_render()
    elif cmd == "add-ticket":
        if len(sys.argv) < 5:
            print(
                "Usage: slate_manager.py add-ticket <pos> <ticket_id> <title> [--bug]"
            )
            sys.exit(2)
        cmd_add_ticket(
            int(sys.argv[2]), sys.argv[3], sys.argv[4], is_bug="--bug" in sys.argv
        )
    elif cmd == "close-ticket":
        if len(sys.argv) < 3:
            print("Usage: slate_manager.py close-ticket <ticket_id>")
            sys.exit(2)
        cmd_close_ticket(sys.argv[2])
    elif cmd == "advance":
        cmd_advance()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()
