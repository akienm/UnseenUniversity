"""
annotator.py — Build palace.codebase.unseen_university.* nodes from clan.code_index.

Reads symbol rows from clan.code_index, groups by file path, calls Haiku for a
one-line problem_signature per module, and upserts into clan.memories.

Modes:
  full_build — process all files in clan.code_index
  nightly    — process only files updated in the last 24 hours

Entry point (CLI):
  python3 -m unseen_university.devices.classifier.annotator [--mode full_build|nightly] [--dry-run]

Called by NannyOggDevice as action_type="run_annotator" on the nightly schedule.

D-classifier-device-architecture-2026-06-12
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import hashlib
import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_OR_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_HAIKU_MODEL = "anthropic/claude-haiku-4-5"
_MAX_SYMBOLS_PER_MODULE = 30  # cap to keep prompt small


@dataclass
class ModuleInfo:
    path: str
    dotted_id: str          # palace node ID
    symbols: list[dict]     # [{symbol, kind, summary}]
    existing_sig: str = ""  # problem_signature from prior run, if any


def _path_to_dotted(path: str) -> str:
    """'devices/granny/daemon.py' → 'palace.codebase.unseen_university.devices.granny.daemon'"""
    without_ext = Path(path).with_suffix("").as_posix()
    return "palace.codebase.unseen_university." + without_ext.replace("/", ".")


def _content_hash(sig: str) -> str:
    return hashlib.md5(sig.encode()).hexdigest()


# ── LLM signature generation ──────────────────────────────────────────────────

def _haiku_problem_signature(path: str, symbols: list[dict]) -> str:
    """
    Call Haiku to generate a one-line problem_signature for a module.
    Returns a fallback summary on any error.
    """
    if not _OR_API_KEY:
        return _fallback_signature(path, symbols)

    lines = [f"File: {path}", "Symbols:"]
    for s in symbols[:_MAX_SYMBOLS_PER_MODULE]:
        lines.append(f"  [{s['kind']}] {s['symbol']}: {s['summary'][:120]}")
    prompt = "\n".join(lines) + (
        "\n\nRespond with ONE LINE only: a problem_signature describing what "
        "this module handles (e.g. 'handles: IMAP bus dispatch, envelope routing, "
        "connection lifecycle'). No preamble, no punctuation at end."
    )

    payload = json.dumps({
        "model": _HAIKU_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 80,
        "temperature": 0.0,
    }).encode()

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {_OR_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        if text:
            log.debug("annotator: haiku sig for %s: %s", path, text[:80])
            return text
    except Exception as exc:
        log.warning("annotator: haiku failed for %s: %s — using fallback", path, exc)

    return _fallback_signature(path, symbols)


def _fallback_signature(path: str, symbols: list[dict]) -> str:
    """Build a rule-based signature when LLM is unavailable."""
    classes = [s["symbol"] for s in symbols if s["kind"] == "class"]
    fns = [s["symbol"] for s in symbols if s["kind"] in ("function", "async_function")][:5]
    parts = []
    if classes:
        parts.append(f"classes: {', '.join(classes[:3])}")
    if fns:
        parts.append(f"functions: {', '.join(fns)}")
    if not parts:
        parts.append(Path(path).stem)
    return "handles: " + "; ".join(parts)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _query_modules(
    db_url: str,
    since_hours: int | None = None,
    file_paths: list[str] | None = None,
) -> list[ModuleInfo]:
    """
    Fetch symbol rows from clan.code_index, grouped by path.

    Priority:
      file_paths — process only these specific paths (delta mode)
      since_hours — process files updated in the last N hours (nightly mode)
      neither — process all files (full_build mode)
    """
    import psycopg2
    import psycopg2.extras

    modules: dict[str, ModuleInfo] = {}

    with psycopg2.connect(db_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if file_paths is not None:
                if not file_paths:
                    return []
                cur.execute(
                    "SELECT path, symbol, kind, summary FROM clan.code_index WHERE path = ANY(%s)",
                    (file_paths,),
                )
            elif since_hours:
                cur.execute(
                    """
                    SELECT DISTINCT path FROM clan.code_index
                    WHERE updated_at > now() - interval '%s hours'
                    """,
                    (since_hours,),
                )
                paths = [r["path"] for r in cur.fetchall()]
                if not paths:
                    return []
                cur.execute(
                    "SELECT path, symbol, kind, summary FROM clan.code_index WHERE path = ANY(%s)",
                    (paths,),
                )
            else:
                cur.execute("SELECT path, symbol, kind, summary FROM clan.code_index")

            for row in cur.fetchall():
                p = row["path"]
                if p not in modules:
                    modules[p] = ModuleInfo(
                        path=p,
                        dotted_id=_path_to_dotted(p),
                        symbols=[],
                    )
                modules[p].symbols.append({
                    "symbol": row["symbol"],
                    "kind": row["kind"],
                    "summary": row["summary"] or "",
                })

    return list(modules.values())


def _upsert_memory(conn: Any, node: ModuleInfo, sig: str) -> str:
    """
    Upsert a palace.codebase.* row into clan.memories.
    Returns 'inserted' or 'updated'.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    narrative_lines = [f"File: {node.path}", "", "Symbols:"]
    for s in node.symbols[:_MAX_SYMBOLS_PER_MODULE]:
        narrative_lines.append(f"  [{s['kind']}] {s['symbol']}: {s['summary'][:200]}")
    narrative = "\n".join(narrative_lines)

    metadata = json.dumps({"problem_signature": sig, "file_path": node.path})

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM clan.memories WHERE id = %s",
            (node.dotted_id,),
        )
        exists = cur.fetchone() is not None

        if exists:
            cur.execute(
                """UPDATE clan.memories
                   SET narrative = %s, metadata = %s::jsonb, timestamp = %s
                   WHERE id = %s""",
                (narrative, metadata, now_iso, node.dotted_id),
            )
            return "updated"
        else:
            cur.execute(
                """INSERT INTO clan.memories
                   (id, narrative, memory_type, metadata, timestamp, valence, arousal, dominance)
                   VALUES (%s, %s, %s, %s::jsonb, %s, 0.0, 0.0, 0.0)""",
                (node.dotted_id, narrative, "codebase_module", metadata, now_iso),
            )
            return "inserted"


# ── Main sweep ────────────────────────────────────────────────────────────────

def run_annotator(
    db_url: str | None = None,
    mode: str = "nightly",
    dry_run: bool = False,
    file_paths: list[str] | None = None,
) -> dict[str, int]:
    """
    Run the annotation sweep.

    mode='full_build': process all files in clan.code_index.
    mode='nightly': process only files updated in the last 24 hours.
    file_paths: when set, override mode — process only these specific files (delta mode).

    Returns {'modules': N, 'inserted': N, 'updated': N, 'errors': N}.
    """
    import psycopg2

    db_url = db_url or home_db_url()
    if file_paths is not None:
        since_hours = None
        log.info("annotator: mode=delta files=%d dry_run=%s", len(file_paths), dry_run)
    else:
        since_hours = None if mode == "full_build" else 24
        log.info("annotator: mode=%s dry_run=%s", mode, dry_run)

    modules = _query_modules(db_url, since_hours=since_hours, file_paths=file_paths)
    log.info("annotator: %d modules to process", len(modules))

    counts = {"modules": len(modules), "inserted": 0, "updated": 0, "errors": 0}
    if dry_run or not modules:
        return counts

    try:
        conn = psycopg2.connect(db_url)
    except Exception as exc:
        log.error("annotator: DB connect failed: %s", exc)
        counts["errors"] = len(modules)
        return counts

    try:
        for mod in modules:
            try:
                sig = _haiku_problem_signature(mod.path, mod.symbols)
                action = _upsert_memory(conn, mod, sig)
                conn.commit()
                counts[action] += 1
                log.info(
                    "annotator: %s|path=%s|sig=%s",
                    action, mod.path, sig[:80],
                )
            except Exception as exc:
                conn.rollback()
                counts["errors"] += 1
                log.error("annotator: error on %s: %s", mod.path, exc)
    finally:
        conn.close()

    return counts


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="nightly",
        choices=["full_build", "nightly"],
        help="full_build: all files; nightly: files updated in last 24h",
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = run_annotator(db_url=args.db_url, mode=args.mode, dry_run=args.dry_run)
    action = "dry-run" if args.dry_run else args.mode
    print(
        f"{action}: modules={result['modules']} inserted={result['inserted']} "
        f"updated={result['updated']} errors={result['errors']}"
    )
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
