#!/usr/bin/env python3
"""
export_chat.py — Render CC session transcript(s) to per-day markdown files.

Each message is routed to `YYYY-MM-DD.md` based on its *own* timestamp. Long-lived
sessions that span days contribute to multiple day-files. Day-files are rebuilt
from scratch on each invocation as the union of every session's contribution for
that date — so the output is idempotent.

Usage:
    export_chat.py              — refresh day-files touched by the newest session
    export_chat.py --session ID — refresh day-files touched by a specific session
    export_chat.py --all        — rebuild every day-file from the full transcript dir
    export_chat.py --dry-run    — report what would be written, don't touch disk

Source:  ~/.claude/projects/-home-akien-TheIgors/<session-id>.jsonl
Output:  $UNSEEN_UNIVERSITY_HOME/logs/CC.0/YYYY-MM-DD.md
         (default: ~/.unseen_university/logs/CC.0/)
         Override with CLAUDE_CHAT_LOGS_DIR env var.

Recovery purpose — if something scrolls off the top of the chat, run /export-chat
to get a persistent copy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# T-timestamp-format-normalization: display local time in human-readable sections
try:
    from lab.claudecode.ts_format import format_display, parse_iso
except ImportError:
    # Fallback if run standalone (e.g. python3 lab/claudecode/export_chat.py)
    def parse_iso(ts: str) -> datetime:  # type: ignore[misc]
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def format_display(dt: datetime) -> str:  # type: ignore[misc]
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
TRANSCRIPT_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(_CLAUDE_PROJECTS)))


def project_transcripts(projects_root: Path) -> list[Path]:
    """Return transcript .jsonl files sorted oldest→newest by mtime.

    Claude Code stores transcripts one level down from the projects root:
    ``<projects_root>/<project-slug>/<session-id>.jsonl`` where ``<project-slug>``
    is the cwd with every ``/`` replaced by ``-`` (e.g. cwd
    ``/home/akien/dev/src/UnseenUniversity`` → ``-home-akien-dev-src-UnseenUniversity``).
    Globbing the projects root directly (``*.jsonl``) finds nothing because the
    root holds only those subdirectories — that was the "No transcripts found" bug.

    Prefer the current cwd's project subdir so the default ("most recently
    modified = the current session") really means *this* session and not a more
    recently touched transcript from a parallel session in another repo. Fall
    back to every project subdir when the cwd subdir is missing or empty, so the
    tool still works when run from an unexpected cwd.
    """
    slug = str(Path.cwd()).replace("/", "-")
    preferred = projects_root / slug
    if preferred.is_dir():
        scoped = list(preferred.glob("*.jsonl"))
        if scoped:
            return sorted(scoped, key=lambda p: p.stat().st_mtime)
    return sorted(projects_root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
# T-cc-script-dead-code-sweep: parameterize output dir so this isn't
# user-hostile across checkouts. Default keeps the prior behavior.
_ADC_HOME = Path(
    os.environ.get("UNSEEN_UNIVERSITY_HOME", str(Path.home() / ".unseen_university"))
)
OUTPUT_DIR = Path(
    os.environ.get(
        "CLAUDE_CHAT_LOGS_DIR",
        str(_ADC_HOME / "logs" / "CC.0"),
    )
)


def _render_message(msg: dict) -> str:
    """Render one transcript message to markdown. Returns '' for uninteresting ones."""
    mtype = msg.get("type")
    ts_raw = msg.get("timestamp", "")
    try:
        ts = format_display(parse_iso(ts_raw)) if ts_raw else ""
    except (ValueError, TypeError):
        ts = ts_raw  # fallback to raw if unparseable
    if mtype == "user":
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_result":
                        tc = c.get("content", "")
                        if isinstance(tc, list):
                            tc = " ".join(
                                p.get("text", "") for p in tc if isinstance(p, dict)
                            )
                        elide = str(tc)[:200].replace("\n", " ")
                        parts.append(
                            f"_[tool result: {elide}{'...' if len(str(tc)) > 200 else ''}]_"
                        )
                elif isinstance(c, str):
                    parts.append(c)
            content = "\n".join(p for p in parts if p)
        if not content:
            return ""
        return f"\n### User — {ts}\n\n{content}\n"
    if mtype == "assistant":
        msg_body = msg.get("message", {})
        content = msg_body.get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        tname = c.get("name", "?")
                        tin = c.get("input", {})
                        summary = json.dumps(tin)[:200].replace("\n", " ")
                        parts.append(
                            f"_[tool: {tname}({summary}{'...' if len(json.dumps(tin)) > 200 else ''})]_"
                        )
            content = "\n".join(p for p in parts if p)
        elif not isinstance(content, str):
            return ""
        if not content:
            return ""
        return f"\n### Assistant — {ts}\n\n{content}\n"
    return ""


def _local_date_of(ts: str) -> str | None:
    """Parse an ISO timestamp, return local YYYY-MM-DD, or None if unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().strftime("%Y-%m-%d")


def partition_session_by_day(path: Path) -> dict[str, list[str]]:
    """Read one transcript and return {YYYY-MM-DD: [rendered_markdown_block, ...]}.

    Messages without a timestamp attach to the most recent known date in the
    session. Messages before any timestamped record are skipped.
    """
    by_day: dict[str, list[str]] = {}
    current_date: str | None = None
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_date = _local_date_of(msg.get("timestamp", ""))
            if ts_date:
                current_date = ts_date
            if current_date is None:
                continue
            rendered = _render_message(msg)
            if rendered:
                by_day.setdefault(current_date, []).append(rendered)
    return by_day


def render_day_file(date_str: str, per_session: list[tuple[str, list[str]]]) -> str:
    """Render a day-file as union of session contributions, ordered by session id."""
    parts = [
        f"# Chat log — {date_str}\n",
        f"\n_rendered {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n",
    ]
    for session_id, blocks in sorted(per_session, key=lambda x: x[0]):
        parts.append(f"\n---\n\n## Session {session_id}\n")
        parts.extend(blocks)
        parts.append(f"\n_({len(blocks)} messages rendered for this day)_\n")
    return "".join(parts)


def resolve_target_sessions(
    all_transcripts: list[Path], session_id: str | None, all_mode: bool
) -> list[Path]:
    files = all_transcripts
    if not files:
        print("No transcripts found.", file=sys.stderr)
        sys.exit(1)
    if all_mode:
        return files
    if session_id:
        matches = [p for p in files if p.stem == session_id]
        if not matches:
            print(f"No transcript for session {session_id}", file=sys.stderr)
            sys.exit(1)
        return matches
    return [files[-1]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="Specific session id (without .jsonl)")
    parser.add_argument(
        "--all", action="store_true", help="Rebuild every day-file from all transcripts"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_transcripts = project_transcripts(TRANSCRIPT_DIR)
    if not all_transcripts:
        print("No transcripts found.", file=sys.stderr)
        sys.exit(1)

    targets = resolve_target_sessions(all_transcripts, args.session, args.all)

    # Partition every session once — we need the other sessions' contributions
    # when rewriting the day-files touched by the target sessions.
    all_partitions: dict[str, dict[str, list[str]]] = {}
    for path in all_transcripts:
        all_partitions[path.stem] = partition_session_by_day(path)

    # Days touched by the target set = days we need to rewrite.
    days_to_refresh: set[str] = set()
    for path in targets:
        days_to_refresh.update(all_partitions[path.stem].keys())

    if not days_to_refresh:
        print("No dated messages in target session(s) — nothing to write.")
        return

    if not args.dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for date_str in sorted(days_to_refresh):
        per_session: list[tuple[str, list[str]]] = []
        for sid, day_map in all_partitions.items():
            blocks = day_map.get(date_str)
            if blocks:
                per_session.append((sid, blocks))
        content = render_day_file(date_str, per_session)
        out_path = OUTPUT_DIR / f"{date_str}.md"
        if args.dry_run:
            print(f"[dry-run] {len(content)} bytes → {out_path}")
        else:
            out_path.write_text(content)
            print(f"wrote {len(content)} bytes → {out_path}")


if __name__ == "__main__":
    main()
