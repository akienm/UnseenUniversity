#!/usr/bin/env python3
"""Pilot migrator: lab/design_docs/decisions/*.md  ->  memory store `decisions/`.

This is the TEMPLATE the bulk (Haiku) migration generalizes. It demonstrates the
locked policy end to end against real records:

  - PROJECTION not relocation: reads the source, writes a projection, deletes
    nothing. lab/design_docs/decisions/ stays authoritative this pass.
  - IDEMPOTENT: stamp derived from the SEMANTIC id via stamp_for_day_only(), so a
    re-run overwrites in place — never duplicates. (These docs are day-only:
    a date in frontmatter, no clock time.)
  - SEMANTIC-ID LINKS: spawned_tickets/related are parsed into links as T-…/D-…
    tokens, never filenames.
  - PROVENANCE: body.source records where the projection came from, so the later
    dedup-by-semantic-id + canonical-source cutover has what it needs.

Run:  python3 devlab/claudecode/migrate_decisions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import os
import re

from memory_emit import emit, stamp_for_day_only

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
SRC_DIR = os.path.join(_REPO, "lab", "design_docs", "decisions")

_FIELD_RE = re.compile(r"^\*\*(\w+):\*\*\s*(.*)$")
_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TICKET_TOK = re.compile(r"\bT-[a-z0-9][a-z0-9-]+", re.I)
_DECISION_TOK = re.compile(r"\b[CD]-[a-z0-9][a-z0-9-]+", re.I)


def parse_doc(path: str) -> dict:
    """Pull frontmatter fields + raw text out of a decision markdown doc."""
    with open(path) as f:
        text = f.read()
    fields = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
    return {"text": text, "fields": fields}


def date_for(stem: str, fields: dict, path: str) -> str:
    """yyyymmdd from frontmatter date, else from the filename, else file mtime."""
    raw = fields.get("date", "")
    m = _DATE_IN_NAME.search(raw) or _DATE_IN_NAME.search(stem)
    if m:
        return m.group(1) + m.group(2) + m.group(3)
    # last resort for date-less C-* docs: keep it deterministic via mtime day
    import datetime
    d = datetime.date.fromtimestamp(os.path.getmtime(path))
    return d.strftime("%Y%m%d")


def links_for(stem: str, fields: dict) -> dict:
    """spawned_tickets/related -> semantic-id links. The doc's own id is the anchor."""
    blob = " ".join(fields.get(k, "") for k in ("spawned_tickets", "related"))
    tickets = sorted(set(_TICKET_TOK.findall(blob)))
    decisions = sorted(set(d for d in _DECISION_TOK.findall(blob) if d != stem))
    decisions = sorted(set(decisions) | {stem})  # always link the doc's own id
    return {"tickets": tickets, "decisions": decisions}


def migrate_one(path: str, dry_run: bool = False) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]  # the semantic id (D-…/C-…)
    doc = parse_doc(path)
    fields = doc["fields"]
    date = date_for(stem, fields, path)
    stamp = stamp_for_day_only(stem, date)
    body = {
        "decision_id": stem,
        "title": fields.get("title", stem),
        "status": fields.get("status", "unknown"),
        "date": fields.get("date", date),
        "source": os.path.relpath(path, _REPO),
        "text": doc["text"],
    }
    if dry_run:
        return f"[dry] decisions/cc.0.{stem}.{stamp}.json"
    return emit("decisions", "cc.0", body, kind="decision",
                namespace=[stem], links=links_for(stem, fields), stamp=stamp)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    paths = sorted(glob.glob(os.path.join(SRC_DIR, "*.md")))
    out = [migrate_one(p, args.dry_run) for p in paths]
    print(f"{'(dry) ' if args.dry_run else ''}migrated {len(out)} decision docs")
    for line in out[:3]:
        print("  e.g.", line)


if __name__ == "__main__":
    main()
