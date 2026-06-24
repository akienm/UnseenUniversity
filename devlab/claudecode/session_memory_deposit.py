"""
session_memory_deposit.py — Deposit today's slate content into clan.memories.

Called during /autocompact (session close). Extracts Done+Notes sections from
today's slate and writes a clan.memories row. The auto-embed trigger on
clan.memories then queues it for semantic indexing, making session content
findable via uurecall.

Usage:
    python3 session_memory_deposit.py [YYYYMMDD]   # defaults to today

D-semantic-indexing-2026-06-09
"""
from __future__ import annotations
from unseen_university._uu_root import uu_home

import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from unseen_university import slate_store

_IGOR_HOME = Path(uu_home())
_DB_URL = os.environ.get("UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
_MAX_CONTENT = 4000


def _read_slate(datestamp: str) -> str | None:
    slate = slate_store.slate_path(datestamp)
    if not slate.exists():
        return None
    return slate.read_text(encoding="utf-8")


def _extract_sections(text: str) -> str:
    """Extract Done today + Notes sections from slate text."""
    parts = []
    for section in ("## Done today", "## Notes", "## In-flight"):
        m = re.search(
            rf"({re.escape(section)}\s*\n)(.*?)(?=\n## |\Z)",
            text,
            re.DOTALL,
        )
        if m:
            content = m.group(2).strip()
            if content and content.upper() != "NONE":
                parts.append(f"{section}\n{content}")
    return "\n\n".join(parts)


def deposit(datestamp: str | None = None, db_url: str | None = None) -> dict:
    """Write session content to clan.memories. Returns status dict."""
    db_url = db_url or _DB_URL
    datestamp = datestamp or datetime.now(timezone.utc).strftime("%Y%m%d")
    date_str = f"{datestamp[:4]}-{datestamp[4:6]}-{datestamp[6:]}"

    slate_text = _read_slate(datestamp)
    if not slate_text:
        return {"ok": False, "reason": f"slate not found: {datestamp}.slate.txt"}

    content = _extract_sections(slate_text)
    if not content:
        return {"ok": False, "reason": "no usable content in Done/Notes/In-flight sections"}

    content = content[:_MAX_CONTENT]
    memory_id = f"SESSION_{datestamp}_{uuid.uuid4().hex[:8].upper()}"
    narrative = f"CC.0 session {date_str}:\n\n{content}"
    now = datetime.now(timezone.utc).isoformat()

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clan.memories
                    (id, narrative, memory_type, source, source_agent, timestamp, updated_at)
                VALUES (%s, %s, 'EPISODIC', 'cc0_session', 'CC.0', %s, %s)
                ON CONFLICT (id) DO UPDATE SET narrative = EXCLUDED.narrative, updated_at = EXCLUDED.updated_at
                """,
                (memory_id, narrative, now, now),
            )
        conn.commit()
        return {"ok": True, "memory_id": memory_id, "content_len": len(content)}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    finally:
        conn.close()


if __name__ == "__main__":
    datestamp = sys.argv[1] if len(sys.argv) > 1 else None
    result = deposit(datestamp)
    if result["ok"]:
        print(f"deposited: {result['memory_id']} ({result['content_len']} chars)")
    else:
        print(f"SKIP: {result['reason']}", file=sys.stderr)
        sys.exit(0)  # non-fatal — autocompact should not abort on deposit failure
