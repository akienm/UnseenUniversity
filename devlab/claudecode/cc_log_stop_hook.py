#!/usr/bin/env python3
# author-model: sonnet
"""
cc_log_stop_hook.py — Stop hook driver: ingest CC session transcript via ChatLogHandler.

Finds all CC session JSONLs modified within the last 48 hours across all project
dirs, ingests them through ChatLogHandler, and flushes to date-partitioned markdown
at ~/.unseen_university/logs/CC.0/YYYY-MM-DD.md.

Ingesting all recent sessions (not just the newest) ensures that when multiple
sessions touch the same day, none are lost on flush().

Replaces chat_log_formatter.py. Safe to run manually.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def _recent_jsonls(hours: int = 48) -> list[Path]:
    """Return all session jsonl files modified within the last `hours` hours."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return []
    cutoff = time.time() - hours * 3600
    files = [p for p in projects_dir.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def main() -> int:
    jsonls = _recent_jsonls()
    if not jsonls:
        return 0
    uu_root = Path(__file__).resolve().parents[2]
    if str(uu_root) not in sys.path:
        sys.path.insert(0, str(uu_root))
    try:
        from unseen_university.devices.claude.chat_log_handler import ChatLogHandler, ingest_session
    except ImportError as e:
        print(f"cc_log_stop_hook: import failed ({e})", file=sys.stderr)
        return 1
    handler = ChatLogHandler()
    for jsonl in jsonls:
        ingest_session(jsonl, handler)
    handler.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
