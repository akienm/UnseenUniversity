#!/usr/bin/env python3
"""uuquestions — surface tickets with open design questions.

Shows:
  - status = open_questions  (Q1: present without matching A1:)
  - status = design          (needs design review before sprint)

Usage: uuquestions [--design-only | --questions-only]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2
import psycopg2.extras

PG = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _first_question(description: str) -> str | None:
    """Extract first Q1: / Q2: line from ticket description."""
    if not description:
        return None
    m = re.search(r'Q\d+:\s*(.+)', description)
    return m.group(0).strip() if m else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="uuquestions", add_help=False)
    p.add_argument("--design-only", action="store_true")
    p.add_argument("--questions-only", action="store_true")
    args = p.parse_args(argv)

    if args.design_only:
        statuses = ["design"]
    elif args.questions_only:
        statuses = ["open_questions"]
    else:
        statuses = ["open_questions", "design"]

    try:
        conn = psycopg2.connect(PG)
    except Exception as e:
        print(f"DB unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT metadata
            FROM clan.memories
            WHERE parent_id = 'TICKETS_ROOT'
              AND metadata->>'kind' = 'ticket'
              AND metadata->>'status' = ANY(%s)
            ORDER BY
              CASE metadata->>'status'
                WHEN 'open_questions' THEN 0
                WHEN 'design' THEN 1
                ELSE 2
              END,
              (metadata->>'priority')::float DESC NULLS LAST,
              metadata->>'id'
            """,
            (statuses,),
        )
        rows = [r["metadata"] for r in cur.fetchall()]
    conn.close()

    if not rows:
        print("(none)")
        sys.exit(0)

    current_status = None
    for t in rows:
        status = t.get("status", "?")
        if status != current_status:
            current_status = status
            if status == "open_questions":
                print("OPEN QUESTIONS:")
            else:
                print(f"\n{status.upper()}:")

        tid = t.get("id", "?")
        size = t.get("size", "?")
        title = t.get("title", "")
        if title.startswith("[") and "]" in title:
            title = title[title.index("]") + 1:].strip()

        icon = "❓" if status == "open_questions" else "📐"
        print(f"  {icon} {tid} ({size}) — {title}")

        q = _first_question(t.get("description", ""))
        if q:
            print(f"       {q}")


if __name__ == "__main__":
    main(sys.argv[1:])
