"""
contrib.py — Global KB contribution pipeline for the Archivist.

Identifies locally-proven patterns (hit_count >= threshold), strips
instance-specific context, stages them for human review, and posts
CONTRIB_CANDIDATE to the shared channel.

Flow:
  scan() → detect_candidates() → strip_context() → stage()
         → post_to_channel()

Staging area: ~/.unseen_university/archivist/contrib-staging/<id>.json
  (gitignored from UU repo; human-readable JSON)

Global KB format: JSONL per unseen_university/global_kb.py schema.
  id, title, type, tags, content, version, source

Human ships:  uu contrib list    — show staged candidates
              uu contrib submit <id>  — print diff / open PR
              uu contrib scan    — detect + stage new candidates
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_DEFAULT_HIT_THRESHOLD = int(os.environ.get("ARCHIVIST_CONTRIB_THRESHOLD", "5"))
_STAGING_DIR = Path(
    os.environ.get(
        "ARCHIVIST_CONTRIB_STAGING",
        str(Path.home() / ".unseen_university" / "archivist" / "contrib-staging"),
    )
)

# Patterns that mark instance-specific content to be stripped
_STRIP_PATTERNS = [
    (r"~?/home/[a-z][a-zA-Z0-9_-]*/[^\s]+", "<instance-path>"),
    (r"~/[^\s]+", "<home-path>"),
    (r"\bIgor-[a-z]+-\d{4}\b", "<instance-id>"),
    (r"\bT-[a-z][a-z0-9-]+\b", "<ticket-id>"),
    (r"\bD-[a-z][a-z0-9-]+-\d{4}-\d{2}-\d{2}\b", "<decision-id>"),
    (r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b", "<email>"),
    # Skip credential-shaped strings (20+ hex chars or key= patterns)
    (r"\bapi_key\s*=\s*['\"][^'\"]{10,}['\"]", "<api-key>"),
    (r"\bpassword\s*=\s*['\"][^'\"]{4,}['\"]", "<password>"),
]


# ── Candidate detection ────────────────────────────────────────────────────────


def detect_candidates(db_url: str, threshold: int = _DEFAULT_HIT_THRESHOLD) -> list[dict]:
    """Query archivist.knowledge_patterns for patterns that crossed the hit threshold.

    Returns list of row dicts with: id, pattern_hash, pattern_text, response_text,
    hit_count, created_at.
    """
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, pattern_hash, pattern_text, response_text, hit_count,
                          created_at, last_hit_at
                   FROM archivist.knowledge_patterns
                   WHERE hit_count >= %s
                   ORDER BY hit_count DESC""",
                (threshold,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.warning("archivist.contrib: detect_candidates failed: %s", e)
        return []


# ── Context stripping ──────────────────────────────────────────────────────────


def strip_context(text: str) -> str:
    """Remove instance-specific paths, IDs, and credentials from text."""
    for pattern, replacement in _STRIP_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _make_contrib_id(pattern_hash: str) -> str:
    """Short stable ID for a contrib candidate."""
    return f"PC-{pattern_hash[:8]}"


def _is_already_staged(contrib_id: str) -> bool:
    candidate_path = _STAGING_DIR / f"{contrib_id}.json"
    return candidate_path.exists()


# ── Staging ────────────────────────────────────────────────────────────────────


def stage_candidate(row: dict, staging_dir: Path | None = None) -> dict | None:
    """Convert a pattern row to a global-KB-format record and write to staging area.

    Returns the staged record dict, or None if already staged or not safe.
    """
    staging = staging_dir or _STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)

    contrib_id = _make_contrib_id(row["pattern_hash"])
    if (staging / f"{contrib_id}.json").exists():
        log.debug("archivist.contrib: %s already staged — skipping", contrib_id)
        return None

    pattern_clean = strip_context(row.get("pattern_text", ""))
    response_clean = strip_context(row.get("response_text", ""))

    # Refuse to stage if stripping left dangerous-looking content
    if re.search(r"[a-f0-9]{40,}", pattern_clean + response_clean, re.I):
        log.warning("archivist.contrib: %s looks like it contains a long hex token — skipping", contrib_id)
        return None

    content = f"## Pattern\n{pattern_clean}\n\n## Response\n{response_clean}"
    record = {
        "id": contrib_id,
        "title": pattern_clean[:60].replace("\n", " "),
        "type": "pattern",
        "tags": ["Archivist", "Proposed"],
        "content": content,
        "version": "1.0",
        "source": "archivist-local",
        "origin_hit_count": row.get("hit_count", 0),
        "staged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (staging / f"{contrib_id}.json").write_text(json.dumps(record, indent=2))
    log.info("archivist.contrib: staged %s (hit_count=%d)", contrib_id, row.get("hit_count", 0))
    return record


# ── Channel notification ───────────────────────────────────────────────────────


def post_contrib_candidate(record: dict, db_url: str | None = None) -> None:
    """Post CONTRIB_CANDIDATE to the shared channel. Best-effort."""
    contrib_id = record.get("id", "?")
    title = record.get("title", "")[:60]
    hit_count = record.get("origin_hit_count", 0)
    msg = f"CONTRIB_CANDIDATE|id={contrib_id}|hits={hit_count}|title={title[:50]}"
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(msg, author="archivist", channel="shared")
    except Exception as e:
        log.debug("archivist.contrib: channel post failed (non-fatal): %s", e)


# ── Staging area readers ───────────────────────────────────────────────────────


def list_staged(staging_dir: Path | None = None) -> list[dict]:
    """Return all records in the staging area."""
    staging = staging_dir or _STAGING_DIR
    if not staging.exists():
        return []
    records = []
    for f in sorted(staging.glob("PC-*.json")):
        try:
            records.append(json.loads(f.read_text()))
        except Exception as e:
            log.warning("archivist.contrib: bad staging file %s: %s", f.name, e)
    return records


def get_staged(contrib_id: str, staging_dir: Path | None = None) -> dict | None:
    """Return a single staged record by ID."""
    staging = staging_dir or _STAGING_DIR
    f = staging / f"{contrib_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


# ── PR draft generation ───────────────────────────────────────────────────────


def build_pr_diff(record: dict, kb_local_dir: Path | None = None) -> str:
    """Return a JSONL line diff showing what would be added to the global KB.

    For manual PR creation — prints the diff that should be added to
    global-kb/patterns/proposed.jsonl in the unseen-university-kb repo.
    """
    contrib_id = record.get("id", "?")
    # Strip origin-specific fields before exporting
    export = {
        "id": contrib_id,
        "title": record.get("title", ""),
        "type": record.get("type", "pattern"),
        "tags": record.get("tags", ["Proposed"]),
        "content": record.get("content", ""),
        "version": record.get("version", "1.0"),
        "source": "unseen-university-kb",
        "origin_instance": None,
    }
    jsonl_line = json.dumps(export)

    pr_body = f"""## Proposed addition to global-kb/patterns/proposed.jsonl

Add the following line to `patterns/proposed.jsonl` in the unseen-university-kb repo:

```
{jsonl_line}
```

**Origin:** locally-proven pattern from Archivist (hit_count={record.get('origin_hit_count', '?')})
**Context stripped:** yes — all instance paths, ticket IDs, and credentials removed
**Review:** check content for any remaining instance-specific context before merging
"""
    return pr_body


# ── Scan entrypoint ───────────────────────────────────────────────────────────


def scan(
    db_url: str | None = None,
    threshold: int = _DEFAULT_HIT_THRESHOLD,
    staging_dir: Path | None = None,
) -> list[dict]:
    """Full scan: detect candidates → strip context → stage → notify channel.

    Returns list of newly staged records (skips already-staged).
    """
    if db_url is None:
        db_url = os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )

    candidates = detect_candidates(db_url, threshold)
    log.info("archivist.contrib: found %d candidates at threshold=%d", len(candidates), threshold)

    newly_staged = []
    for row in candidates:
        record = stage_candidate(row, staging_dir)
        if record:
            post_contrib_candidate(record, db_url)
            newly_staged.append(record)

    if newly_staged:
        log.info("archivist.contrib: staged %d new candidates", len(newly_staged))
    return newly_staged
