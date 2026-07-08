#!/usr/bin/env python3
"""memory_emit — the single chokepoint for the filesystem dev-process memory store.

Every record in UnseenUniversity/devlab/runtime/memory/ is ONE pretty-printed JSON
file written through this helper. Hooks and the migration pass both call here, so
the provisional location + envelope live in ONE place — never a sweep across
thousands of files when (per Akien) "I will change it later."

Owner: Hubert (dev-process / decisions / lab). Location is provisional and moves
with lab->devlab. Full spec: devlab/runtime/memory/SPEC.md.

Filename convention:
    <emitter>[.<ns>...].<yyyymmdd>.<hhmmssuuuuuu>.json

PARSE RULE (authoritative): the two dot-segments immediately before `.json` are
ALWAYS the date (yyyymmdd, 8 digits) and the time (hhmmssuuuuuu, 6-digit
microseconds, 12 digits). Everything before them is the dotted emitter + optional
lower-order namespaces. This is what makes a dotted emitter like `cc.0` round-trip.

Search is grep — bodies are plain readable strings, pretty-printed, never base64.

Idempotency: pass the record's ORIGINAL timestamp as `stamp` during migration.
Same stamp -> same filename -> a re-run overwrites in place (atomic), never
duplicates. Day-only sources must supply a deterministic sub-day component.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

# devlab/claudecode/memory_emit.py -> repo root is three dirs up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _memory_root() -> str:
    """Resolve the memory-store root at CALL time so UU_MEMORY_ROOT set at ANY time is
    honored — parity with unseen_university.memory_root.memory_root() and proof_store /
    ticket_store. Was a module constant frozen at import (T-memory-emit-frozen-root):
    an env change after import was ignored, silently splitting the store (writes to the
    frozen default while readers used the new root)."""
    return os.environ.get("UU_MEMORY_ROOT", os.path.join(_REPO, "devlab", "runtime", "memory"))


# Back-compat: some callers import MEMORY_ROOT. It reflects the import-time value; live
# code paths must use _memory_root() (emit() does) so a later env change is honored.
MEMORY_ROOT = _memory_root()

# Folders == categories. Reserved names (judge, chat.cc.0, chat.igor) are kept
# exactly as Akien named them; renames are his call, not this helper's.
CATEGORIES = {
    "architecture", "artifacts", "boot", "chat.cc.0", "chat.igor", "slates",
    "sessions", "decisions", "builder_feedback", "judge", "notes",
    "design_patterns", "projects/acurite", "projects/uu", "projects/swadl",
    "rules", "tickets", "proofs", "intentions",
}

LINK_KINDS = ("decisions", "tickets", "commits", "whys")
_STAMP_RE = re.compile(r"^\d{8}\.\d{12}$")


def now_stamp() -> str:
    """Migration-time stamp. NOT for migration — use the record's original time."""
    return datetime.now().strftime("%Y%m%d.%H%M%S%f")


def stamp_from_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d.%H%M%S%f")


def stamp_for_day_only(record_id: str, date: str) -> str:
    """Deterministic stamp for sources that carry a date but NO clock time
    (slates, decisions_log.dsb rows). The time field is derived from a stable
    hash of the record's SEMANTIC id, so:

      - every migrator (parallel Haiku workers included) derives the SAME stamp
        for the same record -> the same filename -> idempotent overwrite, no dupes;
      - two records from the same day get distinct stamps (collision only on a
        full SHA1 prefix clash across 86.4e9 slots).

    `date` is 'yyyymmdd'. `record_id` is the semantic id (e.g. 'D-foo-2026-06-16',
    a slate date, a ticket id). The derived time is always a VALID clock time so
    _emitted_at_from_stamp() can parse it back.

    This is the ONE part of the convention where being wrong is not a cheap re-run
    (mismatched schemes -> silent near-duplicates), so it lives here in code, never
    as per-migrator prose.
    """
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError(f"date must be 'yyyymmdd', got {date!r}")
    h = int(hashlib.sha1(record_id.encode("utf-8")).hexdigest(), 16)
    micros = h % 1_000_000
    h //= 1_000_000
    sec = h % 60
    h //= 60
    minute = h % 60
    h //= 60
    hour = h % 24
    return f"{date}.{hour:02d}{minute:02d}{sec:02d}{micros:06d}"


def _emitted_at_from_stamp(stamp: str) -> str:
    d, t = stamp.split(".")
    return datetime.strptime(d + t, "%Y%m%d%H%M%S%f").isoformat()


def _empty_links() -> dict:
    return {k: [] for k in LINK_KINDS}


def parse_filename(fname: str) -> dict:
    """Inverse of the filename convention. Returns emitter_ns list + stamp."""
    base = fname[:-5] if fname.endswith(".json") else fname
    segs = base.split(".")
    if len(segs) < 3:
        raise ValueError(f"not a valid emission filename: {fname!r}")
    date, time = segs[-2], segs[-1]
    if not _STAMP_RE.match(f"{date}.{time}"):
        raise ValueError(f"trailing segments are not yyyymmdd.hhmmssuuuuuu: {fname!r}")
    return {"emitter_ns": segs[:-2], "stamp": f"{date}.{time}"}


def emit(category, emitter, body, *, kind=None, namespace=None, links=None,
         stamp=None, emitted_at=None, produced_by=None) -> str:
    """Write one emission. Returns the path written.

    category   one of CATEGORIES (the folder).
    emitter    who emitted it (e.g. 'cc.0', 'igor', 'hubert'). May contain dots.
    body       the record payload (dict) — plain readable strings, grep-friendly.
    kind       semantic record kind ('decision', 'ticket', 'slate', 'chat', ...).
    namespace  optional lower-order namespace segments (list).
    links      dict subset of LINK_KINDS -> list of SEMANTIC ids (D-..., T-...,
               commit sha, why id). Links use semantic ids, not filenames.
    stamp      'yyyymmdd.hhmmssuuuuuu'. Pass the record's ORIGINAL time when
               migrating (idempotent). Defaults to now for live hook writes.
    produced_by  the ONE backward edge (feedback-edges contract): the artifact
               whose CONTENT caused this emission — 'if this is wrong, what should
               be reviewed?'. A store id (D-*/T-*/proof) when one exists, else a
               typed address (session:<id>, skill:<name>@<date>, intent:I-*,
               human:akien). Distinct from `emitter` (the hand) and `links` (an
               undirected bag). When None the chokepoint stamps the honest
               fallback `session:<emitter>` so every NEW artifact carries an edge;
               additive — no reader may require it (legacy artifacts lack it).
    """
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; known: {sorted(CATEGORIES)}")
    ns = [s for s in (namespace or []) if s]
    stamp = stamp or now_stamp()
    if not _STAMP_RE.match(stamp):
        raise ValueError(f"stamp must be 'yyyymmdd.hhmmssuuuuuu', got {stamp!r}")

    merged = _empty_links()
    for k, v in (links or {}).items():
        if k not in LINK_KINDS:
            raise ValueError(f"unknown link kind {k!r}; known: {LINK_KINDS}")
        merged[k] = list(v)

    stem = ".".join([emitter] + ns + [stamp])
    record = {
        "id": stem,
        "emitter": emitter,
        "namespace": ns,
        "category": category,
        "kind": kind,
        "emitted_at": emitted_at or _emitted_at_from_stamp(stamp),
        "links": merged,
        "produced_by": produced_by or f"session:{emitter}",
        "body": body,
    }
    out_dir = os.path.join(_memory_root(), *category.split("/"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, stem + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)  # atomic; same stamp on re-run -> idempotent overwrite
    return path


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Write one dev-process memory emission.")
    ap.add_argument("--category", required=True)
    ap.add_argument("--emitter", required=True)
    ap.add_argument("--kind", default=None)
    ap.add_argument("--namespace", default=None,
                    help="dotted or comma-separated lower-order namespace(s)")
    ap.add_argument("--stamp", default=None,
                    help="yyyymmdd.hhmmssuuuuuu (original time when migrating)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--body", help="inline JSON body")
    g.add_argument("--body-file", help="path to a JSON body file")
    ap.add_argument("--body-text", help="plain text body (wrapped as {\"text\": ...})")
    ap.add_argument("--links", default=None, help="JSON dict of semantic-id links")
    ap.add_argument("--produced-by", default=None,
                    help="the backward edge: the artifact whose content caused this "
                         "emission (D-*/T-*/proof id, or session:/skill:/intent:/human: address)")
    args = ap.parse_args(argv)

    if args.body_file:
        with open(args.body_file) as f:
            body = json.load(f)
    elif args.body:
        body = json.loads(args.body)
    elif args.body_text is not None:
        body = {"text": args.body_text}
    else:
        body = json.load(sys.stdin)

    ns = None
    if args.namespace:
        ns = re.split(r"[.,]", args.namespace)
    links = json.loads(args.links) if args.links else None

    path = emit(args.category, args.emitter, body, kind=args.kind,
                namespace=ns, links=links, stamp=args.stamp,
                produced_by=args.produced_by)
    print(path)


if __name__ == "__main__":
    _main()
