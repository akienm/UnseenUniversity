#!/usr/bin/env python3
"""
cc_nightly_palace_updates.py — Write decision and session nodes to adc.palace.

Two responsibilities:
  1. Decisions: scan lab/design_docs/decisions/D-*.md, upsert each as a
     palace.decisions.* node (idempotent — same file → same node).
  2. Session brief: read today's slate (Done today section) and write a
     palace.sessions.YYYYMMDD.brief node summarising what happened.

Usage:
    python3 cc_nightly_palace_updates.py [--date YYYY-MM-DD] [--dry-run]

Flags:
    --date YYYY-MM-DD   process decisions from this date (default: today)
    --dry-run           print what would be written without touching the DB
    --all               process all decision docs (ignore date filter)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from unseen_university import slate_store

_UU_ROOT = Path(__file__).resolve().parents[2]
_DECISIONS_DIR = _UU_ROOT / "devlab" / "design_docs" / "decisions"
_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
)


# ── Decision doc parsing ───────────────────────────────────────────────────────

def _parse_decision_doc(path: Path) -> dict | None:
    """Parse a D-*.md decision doc into a structured dict.

    Returns None when the file cannot be parsed or lacks required fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    doc: dict = {"path": str(path), "slug": path.stem}

    # Header fields: **title:** / **date:** / **status:** / **spawned_tickets:**
    for field in ("title", "date", "status"):
        m = re.search(rf"\*\*{field}:\*\*\s*(.+)", text, re.IGNORECASE)
        doc[field] = m.group(1).strip() if m else ""

    m = re.search(r"\*\*spawned_tickets:\*\*\s*(.+)", text, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        # Comma-separated, strip parenthetical notes like "(updated)"
        tickets = [re.sub(r"\s*\(.*?\)", "", t).strip() for t in raw.split(",")]
        doc["spawned_tickets"] = [t for t in tickets if t.startswith("T-")]
    else:
        doc["spawned_tickets"] = []

    # Decision narrative section
    m = re.search(r"## Decision narrative\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL | re.IGNORECASE)
    doc["narrative"] = m.group(1).strip() if m else ""

    # Hypothesis section
    m = re.search(r"## Hypothesis\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL | re.IGNORECASE)
    doc["hypothesis"] = m.group(1).strip() if m else ""

    # Measurement signal
    m = re.search(r"## Measurement Signal\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL | re.IGNORECASE)
    doc["measurement_signal"] = m.group(1).strip() if m else ""

    if not doc.get("title") or not doc.get("date"):
        return None

    return doc


def scan_decision_docs(date_filter: str | None = None, all_docs: bool = False) -> list[dict]:
    """Scan lab/design_docs/decisions/ and return parsed decision dicts.

    When date_filter is set (YYYY-MM-DD), only include docs whose **date:**
    field matches. When all_docs=True, return everything.
    """
    if not _DECISIONS_DIR.exists():
        return []

    docs = []
    for path in sorted(_DECISIONS_DIR.glob("D-*.md")):
        doc = _parse_decision_doc(path)
        if doc is None:
            continue
        if not all_docs and date_filter and doc.get("date") != date_filter:
            continue
        docs.append(doc)

    return docs


# ── Palace write ───────────────────────────────────────────────────────────────

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


def write_decision_nodes(docs: list[dict], dry_run: bool = False) -> int:
    """Upsert each decision doc as a palace.decisions.* node. Returns count."""
    if not docs:
        return 0

    written = 0
    for doc in docs:
        slug = doc["slug"].lower()
        path = f"palace.decisions.{slug}"
        title = doc.get("title", slug)
        tickets = doc.get("spawned_tickets", [])
        content = (
            f"## Decision: {title}\n\n"
            f"**Date:** {doc.get('date', '')}\n"
            f"**Status:** {doc.get('status', 'open')}\n"
            f"**Spawned tickets:** {', '.join(tickets) or 'none'}\n\n"
        )
        if doc.get("narrative"):
            content += f"## Narrative\n{doc['narrative']}\n\n"
        if doc.get("hypothesis"):
            content += f"## Hypothesis\n{doc['hypothesis']}\n\n"
        if doc.get("measurement_signal"):
            content += f"## Measurement Signal\n{doc['measurement_signal']}\n\n"

        metadata = {
            "slug": slug,
            "date": doc.get("date", ""),
            "status": doc.get("status", "open"),
            "spawned_tickets": tickets,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if dry_run:
            print(f"[DRY RUN] upsert {path}: {title[:60]}")
            written += 1
            continue

        try:
            import psycopg2
            conn = psycopg2.connect(_DB_URL)
            _palace_upsert(conn, path, title, content, "decision", metadata)
            conn.commit()
            conn.close()
            print(f"  upserted: {path}")
            written += 1
        except Exception as exc:
            print(f"  [warn] palace write failed for {slug}: {exc}", file=sys.stderr)

    return written


# ── Session brief ──────────────────────────────────────────────────────────────

def _read_slate_done(date: str) -> str:
    """Return the Done today section from a slate file, or empty string."""
    datestamp = date.replace("-", "")
    slate = slate_store.slate_path(datestamp)
    if not slate.exists():
        return ""
    try:
        text = slate.read_text(encoding="utf-8")
        m = re.search(r"## Done today\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def _read_slate_notes(date: str) -> str:
    """Return the Notes section from a slate file."""
    datestamp = date.replace("-", "")
    slate = slate_store.slate_path(datestamp)
    if not slate.exists():
        return ""
    try:
        text = slate.read_text(encoding="utf-8")
        m = re.search(r"## Notes\s*\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def write_session_brief(date: str, decision_count: int, dry_run: bool = False) -> bool:
    """Write palace.sessions.DATE.brief from today's slate. Returns True on success."""
    datestamp = date.replace("-", "")
    path = f"palace.sessions.{datestamp}.brief"
    title = f"Session brief {date}"

    done = _read_slate_done(date)
    notes = _read_slate_notes(date)

    content = (
        f"## Session brief — {date}\n\n"
        f"**Decisions filed today:** {decision_count}\n\n"
    )
    if done:
        content += f"## Done today\n{done}\n\n"
    if notes:
        content += f"## Notes\n{notes}\n\n"

    metadata = {
        "date": date,
        "decision_count": decision_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        print(f"[DRY RUN] upsert {path}: {title}")
        return True

    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL)
        _palace_upsert(conn, path, title, content, "session_brief", metadata)
        conn.commit()
        conn.close()
        print(f"  upserted: {path}")
        return True
    except Exception as exc:
        print(f"  [warn] session brief write failed: {exc}", file=sys.stderr)
        return False


# ── Entry point ────────────────────────────────────────────────────────────────

def run(date: str | None = None, dry_run: bool = False, all_docs: bool = False) -> dict:
    """Run the nightly palace update. Returns summary dict."""
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"cc_nightly_palace_updates: date={date} dry_run={dry_run} all={all_docs}")

    docs = scan_decision_docs(date_filter=None if all_docs else date, all_docs=all_docs)
    print(f"  decisions found: {len(docs)}")

    decisions_written = write_decision_nodes(docs, dry_run=dry_run)
    session_ok = write_session_brief(date, decision_count=len(docs), dry_run=dry_run)

    summary = {
        "date": date,
        "decisions_found": len(docs),
        "decisions_written": decisions_written,
        "session_brief_written": session_ok,
    }
    print(
        f"\nsummary: {decisions_written}/{len(docs)} decision nodes written, "
        f"session brief {'ok' if session_ok else 'failed'}"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Date YYYY-MM-DD to filter decisions (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing to DB")
    parser.add_argument("--all", dest="all_docs", action="store_true",
                        help="Process all decision docs regardless of date")
    args = parser.parse_args()

    run(date=args.date, dry_run=args.dry_run, all_docs=args.all_docs)


if __name__ == "__main__":
    main()
