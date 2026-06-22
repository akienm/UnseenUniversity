#!/usr/bin/env python3
"""
cc_nightly_context_prep.py — Prepare tomorrow's context briefing.

Reads today's session artifacts and writes a palace.sessions.DATE+1.brief node
that /context-load can surface on the next session start.

Included in the briefing:
  - In-flight items from today's slate
  - High-priority sprint/triage tickets (priority > 0.6)
  - Design-status tickets (needing design work)
  - Patterns validated today (palace.patterns.*)
  - Pending approvals

Usage:
    python3 cc_nightly_context_prep.py [--date YYYY-MM-DD] [--dry-run]

Flags:
    --date YYYY-MM-DD   base date (default: today); tomorrow = date + 1 day
    --dry-run           print what would be written without touching the DB
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unseen_university import slate_store, ticket_store

_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
)


# ── Slate reading ─────────────────────────────────────────────────────────────

def _read_slate_section(date: str, section: str) -> str:
    """Return a named ## section from the slate, or empty string."""
    datestamp = date.replace("-", "")
    slate = slate_store.slate_path(datestamp)
    if not slate.exists():
        return ""
    try:
        text = slate.read_text(encoding="utf-8")
        m = re.search(rf"## {re.escape(section)}\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


# ── Queue reading ─────────────────────────────────────────────────────────────

def _read_high_priority_tickets(min_priority: float = 0.6) -> list[dict]:
    """Return sprint/triage tickets with priority > min_priority, sorted descending."""
    try:
        wanted = {"sprint", "triage", "in_progress"}
        rows = []
        for t in ticket_store.list():
            if t.get("status") not in wanted:
                continue
            pri = float(t.get("priority") or 0.5)
            if pri <= min_priority:
                continue
            rows.append({
                "id": t.get("id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "size": t.get("size"),
                "priority": pri,
            })
        rows.sort(key=lambda r: r["priority"], reverse=True)
        return rows[:20]
    except Exception:
        return []


def _read_design_tickets() -> list[dict]:
    """Return tickets in 'design' status."""
    try:
        rows = [{"id": t.get("id"), "title": t.get("title")}
                for t in ticket_store.list(status_filter="design")]
        return rows[:10]
    except Exception:
        return []


def _read_pending_approvals() -> list[dict]:
    """Return tickets in 'approval' status."""
    try:
        rows = [{"id": t.get("id"), "title": t.get("title")}
                for t in ticket_store.list(status_filter="approval")]
        return rows[:10]
    except Exception:
        return []


# ── Palace patterns ───────────────────────────────────────────────────────────

def _read_recent_patterns(date: str) -> list[dict]:
    """Return pattern nodes updated today from adc.palace."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(_DB_URL)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT path, title, metadata->>'last_seen' AS last_seen
                FROM adc.palace
                WHERE path LIKE 'palace.patterns.%%'
                  AND updated_at::date = %s::date
                ORDER BY updated_at DESC
                LIMIT 15
                """,
                (date,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ── Palace write ──────────────────────────────────────────────────────────────

def _palace_upsert(conn, path: str, title: str, content: str, node_type: str, metadata: dict) -> None:
    """Upsert one node into adc.palace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
            VALUES (%s, %s, %s, %s, now(), %s::jsonb)
            ON CONFLICT (path) DO UPDATE SET
                title      = EXCLUDED.title,
                content    = EXCLUDED.content,
                updated_at = EXCLUDED.updated_at,
                metadata   = adc.palace.metadata || EXCLUDED.metadata
            """,
            (path, title, content, node_type, json.dumps(metadata)),
        )


# ── Briefing assembly ─────────────────────────────────────────────────────────

def build_briefing(date: str) -> str:
    """Assemble the tomorrow-context briefing from today's artifacts."""
    tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    lines = [f"## Context briefing for {tomorrow}", f"*(Generated from session {date})*", ""]

    # In-flight
    in_flight = _read_slate_section(date, "In-flight")
    lines += ["## In-flight from previous session", in_flight or "NONE", ""]

    # Done today (brief)
    done = _read_slate_section(date, "Done today")
    if done:
        done_lines = [l for l in done.splitlines() if l.strip()][:10]
        lines += ["## Shipped today", "\n".join(done_lines), ""]

    # High-priority tickets
    hp_tickets = _read_high_priority_tickets(min_priority=0.6)
    if hp_tickets:
        lines.append("## High-priority tickets (>0.6)")
        for t in hp_tickets:
            lines.append(f"- [{t['id']}] ({t['size']}) {t['title']} [{t['status']}]")
        lines.append("")

    # Design tickets
    design_tickets = _read_design_tickets()
    if design_tickets:
        lines.append("## Needs design")
        for t in design_tickets:
            lines.append(f"- [{t['id']}] {t['title']}")
        lines.append("")

    # Pending approvals
    approvals = _read_pending_approvals()
    if approvals:
        lines.append("## Pending approvals")
        for t in approvals:
            lines.append(f"- [{t['id']}] {t['title']}")
        lines.append("")

    # Recent patterns
    patterns = _read_recent_patterns(date)
    if patterns:
        lines.append("## Patterns active today")
        for p in patterns:
            lines.append(f"- {p['title']} ({p['path']})")
        lines.append("")

    return "\n".join(lines)


def write_context_brief(date: str, dry_run: bool = False) -> bool:
    """Write palace.sessions.TOMORROW.brief. Returns True on success."""
    tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    datestamp = tomorrow.replace("-", "")
    path = f"palace.sessions.{datestamp}.brief"
    title = f"Context brief for {tomorrow}"

    content = build_briefing(date)

    metadata = {
        "source_date": date,
        "target_date": tomorrow,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        print(f"[DRY RUN] upsert {path}: {title}")
        print(content[:400])
        return True

    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL)
        _palace_upsert(conn, path, title, content, "context_brief", metadata)
        conn.commit()
        conn.close()
        print(f"  upserted: {path}")
        return True
    except Exception as exc:
        print(f"  [warn] context brief write failed: {exc}", file=sys.stderr)
        return False


# ── Entry point ────────────────────────────────────────────────────────────────

def run(date: str | None = None, dry_run: bool = False) -> dict:
    """Run the context prep. Returns summary dict."""
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    tomorrow = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"cc_nightly_context_prep: date={date} tomorrow={tomorrow} dry_run={dry_run}")

    ok = write_context_brief(date, dry_run=dry_run)
    summary = {"date": date, "tomorrow": tomorrow, "context_brief_written": ok}
    print(f"\nsummary: context brief {'ok' if ok else 'failed'}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Base date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing to DB")
    args = parser.parse_args()
    run(date=args.date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
