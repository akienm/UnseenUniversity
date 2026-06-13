#!/usr/bin/env python3
"""
code_indexer.py — Multi-language code index sweep.

Dispatches to language-specific extractors and writes to clan.code_index:
  - Python: AST symbol extraction via devices.nanny.sweeps.code_sweep
  - Shell/bash (.sh): Haiku intent extraction via OpenRouter; writes
    a  # intent: <one-liner>  comment to the file header (idempotent).

Usage:
    python3 lab/claudecode/code_indexer.py [--dry-run] [--repo-root PATH]
    python3 lab/claudecode/code_indexer.py --files a.sh b.sh   # targeted re-index

clan.code_index schema for file_intent rows:
    path          = repo-relative path  (e.g. "config/cc_env.sh")
    symbol        = "__file_intent__"
    kind          = "file_intent"
    summary       = the intent sentence (≤500 chars)
    content_hash  = MD5 of file content AFTER intent comment injection
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
)
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_HAIKU_MODEL = "anthropic/claude-haiku-4-5"
_INTENT_SYMBOL = "__file_intent__"

_SYSTEM_PROMPT = (
    "You are a code documentation assistant. "
    "Given a shell script, write ONE sentence (15 words max) describing what it does. "
    "Output ONLY the sentence — no quotes, no trailing punctuation."
)


# ── Intent extraction ──────────────────────────────────────────────────────────


def _haiku_intent(content: str, rel_path: str) -> str:
    """Call Haiku via OpenRouter to extract a one-line file intent. Returns '' on failure."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.warning("CODE_INDEXER|path=%s|warn=OPENROUTER_API_KEY not set — skipping Haiku", rel_path)
        return ""

    payload = json.dumps({
        "model": _HAIKU_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Script ({rel_path}):\n\n{content[:3000]}"},
        ],
        "temperature": 0.1,
        "max_tokens": 60,
    }).encode()

    req = urllib.request.Request(
        f"{_OPENROUTER_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/akienm/UnseenUniversity",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip().rstrip(".")
        log.info("CODE_INDEXER|path=%s|action=haiku_intent|intent=%r", rel_path, text)
        return text
    except Exception as exc:
        log.warning("CODE_INDEXER|path=%s|warn=Haiku call failed|exc=%s", rel_path, exc)
        return ""


def read_existing_intent(path: Path) -> str:
    """Extract the existing # intent: line from a file, or ''."""
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("# intent:"):
                return line[len("# intent:"):].strip()
    except OSError:
        pass
    return ""


def inject_intent_comment(path: Path, intent: str) -> None:
    """Write/replace the # intent: line in the file header. Idempotent."""
    content = path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines(keepends=True)

    # Strip any existing # intent: line
    lines = [l for l in lines if not l.startswith("# intent:")]

    # Insert after shebang if present, else at the top
    insert_pos = 1 if lines and lines[0].startswith("#!") else 0
    lines.insert(insert_pos, f"# intent: {intent}\n")

    path.write_text("".join(lines), encoding="utf-8")


def file_hash(path: Path) -> str:
    """MD5 of current file content."""
    return hashlib.md5(path.read_bytes()).hexdigest()


# ── Shell sweep ────────────────────────────────────────────────────────────────


def sweep_shell_files(
    repo_root: Path,
    db_url: str,
    dry_run: bool = False,
    files: list[Path] | None = None,
) -> dict:
    """
    Find .sh files, extract Haiku intent, inject # intent: header, upsert to clan.code_index.

    When files is provided, only those paths are processed (targeted re-index).
    content_hash stored in DB is the hash of the file AFTER intent injection.
    """
    counters = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0}

    if files is not None:
        sh_files = [Path(f) for f in files if str(f).endswith(".sh")]
    else:
        sh_files = [
            f for f in repo_root.rglob("*.sh")
            if ".git" not in str(f) and ".venv" not in str(f)
        ]

    if not sh_files:
        log.info("CODE_INDEXER|shell|no .sh files found under %s", repo_root)
        return counters

    log.info("CODE_INDEXER|shell|files=%d", len(sh_files))

    if dry_run:
        for f in sh_files:
            rel = str(f.relative_to(repo_root)) if f.is_absolute() else str(f)
            log.info("CODE_INDEXER|dry_run|path=%s", rel)
            counters["inserted"] += 1
        return counters

    import psycopg2

    conn = psycopg2.connect(db_url)
    try:
        for sh_path in sh_files:
            try:
                rel_path = str(sh_path.relative_to(repo_root)) if sh_path.is_absolute() else str(sh_path)
            except ValueError:
                rel_path = str(sh_path)

            try:
                existing_intent = read_existing_intent(sh_path)
                current_hash = file_hash(sh_path)

                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content_hash FROM clan.code_index WHERE path = %s AND symbol = %s",
                        (rel_path, _INTENT_SYMBOL),
                    )
                    row = cur.fetchone()

                if row and row[0] == current_hash:
                    counters["unchanged"] += 1
                    continue

                # New file or content changed — extract fresh intent
                content = sh_path.read_text(encoding="utf-8", errors="ignore")
                intent = _haiku_intent(content, rel_path) or existing_intent or f"shell script: {sh_path.name}"

                inject_intent_comment(sh_path, intent)
                final_hash = file_hash(sh_path)  # hash after intent injection
                log.info("CODE_INDEXER|action=intent_written|path=%s|intent=%r", rel_path, intent)

                summary = intent[:500]
                with conn.cursor() as cur:
                    if row is None:
                        cur.execute(
                            """INSERT INTO clan.code_index
                               (path, symbol, kind, summary, content_hash, updated_at)
                               VALUES (%s, %s, 'file_intent', %s, %s, now())""",
                            (rel_path, _INTENT_SYMBOL, summary, final_hash),
                        )
                        counters["inserted"] += 1
                        log.info("CODE_INDEXER|action=db_insert|path=%s", rel_path)
                    else:
                        cur.execute(
                            """UPDATE clan.code_index
                               SET summary = %s, content_hash = %s,
                                   embedding = NULL, updated_at = now()
                               WHERE path = %s AND symbol = %s""",
                            (summary, final_hash, rel_path, _INTENT_SYMBOL),
                        )
                        counters["updated"] += 1
                        log.info("CODE_INDEXER|action=db_update|path=%s", rel_path)

                conn.commit()

            except Exception as exc:
                counters["errors"] += 1
                conn.rollback()
                log.error("CODE_INDEXER|path=%s|error=%s", rel_path, exc)

    finally:
        conn.close()

    return counters


# ── Python sweep (delegate to code_sweep) ─────────────────────────────────────


def sweep_python_files(
    repo_root: Path,
    db_url: str,
    dry_run: bool = False,
) -> dict:
    """Delegate Python AST symbol extraction to devices.nanny.sweeps.code_sweep."""
    sys.path.insert(0, str(repo_root))
    try:
        from devices.nanny.sweeps.code_sweep import run_sweep
        return run_sweep(db_url=db_url, repo_root=repo_root, dry_run=dry_run)
    except ImportError as exc:
        log.error("CODE_INDEXER|python_sweep|import_error=%s", exc)
        return {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 1}


# ── Multi-language entry point ─────────────────────────────────────────────────


def run_sweep(
    repo_root: Path | None = None,
    db_url: str | None = None,
    dry_run: bool = False,
    files: list[Path] | None = None,
) -> dict:
    """
    Multi-language dispatch: Python AST sweep + shell intent sweep.
    Returns merged counters across all languages.
    """
    if repo_root is None:
        try:
            from unseen_university._uu_root import uu_root
            repo_root = Path(uu_root())
        except ImportError:
            repo_root = Path(__file__).resolve().parent.parent.parent

    if db_url is None:
        db_url = _DB_URL

    log.info("CODE_INDEXER|repo_root=%s|dry_run=%s", repo_root, dry_run)

    all_counters: dict[str, int] = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0}

    def _merge(c: dict) -> None:
        for k in all_counters:
            all_counters[k] += c.get(k, 0)

    # Python: full repo sweep (or skip if targeted file list is shell-only)
    if files is None or any(not str(f).endswith(".sh") for f in files):
        py = sweep_python_files(repo_root, db_url, dry_run)
        log.info("CODE_INDEXER|python|inserted=%d updated=%d unchanged=%d errors=%d",
                 py["inserted"], py["updated"], py["unchanged"], py["errors"])
        _merge(py)

    # Shell/bash
    sh = sweep_shell_files(repo_root, db_url, dry_run, files=files)
    log.info("CODE_INDEXER|shell|inserted=%d updated=%d unchanged=%d errors=%d",
             sh["inserted"], sh["updated"], sh["unchanged"], sh["errors"])
    _merge(sh)

    return all_counters


# ── CLI ────────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    parser.add_argument("--db-url", default=None, help="Postgres DB URL")
    parser.add_argument("--dry-run", action="store_true", help="Count symbols without writing")
    parser.add_argument("--files", nargs="+", help="Specific files to re-index (targeted mode)")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root = Path(args.repo_root) if args.repo_root else None
    file_paths = [Path(f) for f in args.files] if args.files else None

    result = run_sweep(
        repo_root=root,
        db_url=args.db_url,
        dry_run=args.dry_run,
        files=file_paths,
    )

    action = "dry-run" if args.dry_run else "sweep"
    print(
        f"{action}: inserted={result['inserted']} updated={result['updated']} "
        f"unchanged={result['unchanged']} errors={result['errors']}"
    )
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
