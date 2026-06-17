#!/usr/bin/env python3
"""palace_seed_decisions.py — Seed palace.decisions.* from D-*.md stubs.

Reads all lab/design_docs/decisions/D-*.md files in TheIgors, parses frontmatter
and content, upserts one palace node per file at path palace.decisions.<id>.

Usage:
    python3 scripts/palace_seed_decisions.py                  # dry-run (print only)
    python3 scripts/palace_seed_decisions.py --write          # upsert to palace
    python3 scripts/palace_seed_decisions.py --decisions-dir <path>  # override source
    python3 scripts/palace_seed_decisions.py --schema adc     # target schema (default: adc)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DECISIONS_DIR = _REPO_ROOT / "devlab" / "design_docs" / "decisions"

_UPSERT = """
INSERT INTO {schema}.palace (path, title, content, node_type, updated_at, metadata)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (path) DO UPDATE
    SET title      = EXCLUDED.title,
        content    = EXCLUDED.content,
        node_type  = EXCLUDED.node_type,
        updated_at = EXCLUDED.updated_at,
        metadata   = EXCLUDED.metadata;
"""


def _parse_md(text: str) -> dict:
    """Parse a D-*.md file into a dict with keys: title, date, status, spawned_tickets, body."""
    result: dict = {
        "title": "",
        "date": "",
        "status": "open",
        "spawned_tickets": [],
        "body": text,
    }

    # Pull **key:** value lines from the top of the file
    for line in text.splitlines()[:20]:
        m = re.match(r"^\*\*(\w+(?:_\w+)*):\*\*\s*(.+)$", line)
        if not m:
            continue
        key, val = m.group(1).lower().replace("-", "_"), m.group(2).strip()
        if key == "title":
            result["title"] = val
        elif key == "date":
            result["date"] = val
        elif key == "status":
            result["status"] = val
        elif key == "spawned_tickets":
            result["spawned_tickets"] = [t.strip() for t in val.split(",") if t.strip()]

    return result


def _infer_tags(decision_id: str, parsed: dict) -> list[str]:
    tags = ["decision"]
    if parsed["status"] in ("open", "closed"):
        tags.append(parsed["status"])
    # Area tags from id prefix
    for prefix in ("adc", "igor", "librarian", "swadl", "bus", "imap", "palace"):
        if prefix in decision_id.lower():
            tags.append(prefix)
    return tags


def load_decisions(decisions_dir: Path) -> list[dict]:
    nodes = []
    for f in sorted(decisions_dir.glob("D-*.md")):
        decision_id = f.stem  # e.g. D-adc-phase-0-2026-04-27
        text = f.read_text(errors="replace")
        parsed = _parse_md(text)

        title = parsed["title"] or decision_id
        date_str = parsed["date"] or ""
        status = parsed["status"]
        spawned = parsed["spawned_tickets"]
        tags = _infer_tags(decision_id, parsed)

        # Derive updated_at from the date field or fallback to now
        try:
            updated_at = datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            updated_at = datetime.now(timezone.utc)

        metadata = {
            "tags": tags,
            "status": status,
            "date": date_str,
            "source_file": (
                str(f)
                if not f.is_relative_to(_REPO_ROOT)
                else str(f.relative_to(_REPO_ROOT))
            ),
        }
        if spawned:
            metadata["spawned_tickets"] = spawned

        nodes.append(
            {
                "path": f"palace.decisions.{decision_id}",
                "title": title,
                "content": text,
                "node_type": "decision",
                "updated_at": updated_at,
                "metadata": psycopg2.extras.Json(metadata),
                "_id": decision_id,
            }
        )
    return nodes


def seed(conn, nodes: list[dict], schema: str = "adc") -> int:
    upsert_sql = _UPSERT.format(schema=schema)
    count = 0
    with conn.cursor() as cur:
        for n in nodes:
            cur.execute(
                upsert_sql,
                (
                    n["path"],
                    n["title"],
                    n["content"],
                    n["node_type"],
                    n["updated_at"],
                    n["metadata"],
                ),
            )
            count += 1
    conn.commit()
    return count


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--write", action="store_true", help="Write to palace (default: dry-run)"
    )
    ap.add_argument("--decisions-dir", default=str(_DEFAULT_DECISIONS_DIR))
    ap.add_argument("--schema", default="adc")
    args = ap.parse_args()

    decisions_dir = Path(args.decisions_dir)
    if not decisions_dir.exists():
        print(f"ERROR: decisions dir not found: {decisions_dir}", file=sys.stderr)
        sys.exit(1)

    nodes = load_decisions(decisions_dir)
    print(f"Found {len(nodes)} decision files in {decisions_dir}")

    if not args.write:
        print("Dry-run — first 5 paths:")
        for n in nodes[:5]:
            print(f"  {n['path']}  [{n['_id']}]")
        print("Re-run with --write to upsert.")
        return

    conn = psycopg2.connect(_PG_URL)
    count = seed(conn, nodes, schema=args.schema)
    conn.close()
    print(f"Upserted {count} palace.decisions.* nodes into schema '{args.schema}'.")


if __name__ == "__main__":
    main()
