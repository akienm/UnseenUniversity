#!/usr/bin/env python3
"""session_capture.py — Capture current CC session as palace transcript + summary node.

Reads the session JSONL, strips tool calls/results/thinking blocks, and writes:
  - palace.transcripts.<YYYYMMDD-N>  (text-only transcript)
  - palace.sessions.<YYYYMMDD-N>     (5-10 line summary: decisions, tickets, in-flight)
  - flat-file echoes alongside for disaster recovery

Usage:
    python3 scripts/session_capture.py                        # auto-detect current session
    python3 scripts/session_capture.py --session-id <id>      # explicit session JSONL id
    python3 scripts/session_capture.py --session-file <path>  # explicit JSONL path
    python3 scripts/session_capture.py --summary <text>       # summary text (else auto-drafted)
    python3 scripts/session_capture.py --dry-run              # print without writing
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_ECHO_DIR = Path.home() / ".unseen_university" / "claudecode" / "palace_echo"

_UPSERT = """
INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
VALUES (%s, %s, %s, %s, now(), %s)
ON CONFLICT (path) DO UPDATE
    SET title=EXCLUDED.title, content=EXCLUDED.content,
        node_type=EXCLUDED.node_type, updated_at=EXCLUDED.updated_at,
        metadata=EXCLUDED.metadata;
"""


def _find_latest_session_file() -> Path | None:
    """Return the most recently modified .jsonl in any projects subdir."""
    candidates = []
    for f in _PROJECTS_DIR.rglob("*.jsonl"):
        if f.stat().st_size > 0:
            candidates.append(f)
    return max(candidates, key=lambda f: f.stat().st_mtime) if candidates else None


def _extract_text(content) -> str:
    """Return plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            # skip tool_use, tool_result, thinking, image
        return "\n".join(p for p in parts if p)
    return ""


def extract_transcript(session_file: Path) -> list[dict]:
    """Parse JSONL and return list of {role, text} for human/assistant text turns only."""
    turns = []
    for raw in session_file.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message", {})
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg.get("content", ""))
        if text.strip():
            turns.append({"role": role, "text": text.strip()})
    return turns


def _format_transcript(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        prefix = "Akien" if t["role"] == "user" else "CC"
        # Truncate very long turns (tool result summaries etc.)
        body = t["text"]
        if len(body) > 2000:
            body = body[:2000] + "\n… [truncated]"
        lines.append(f"**{prefix}:** {body}")
    return "\n\n".join(lines)


def _next_session_slot(conn, datestamp: str) -> str:
    """Return the next available palace.sessions.<datestamp>-N slot."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT path FROM adc.palace WHERE path LIKE %s ORDER BY path",
            (f"palace.sessions.{datestamp}-%",),
        )
        existing = [r[0] for r in cur.fetchall()]
    n = len(existing) + 1
    return f"palace.sessions.{datestamp}-{n:02d}"


def _auto_summary(turns: list[dict], session_path: str) -> str:
    """Draft a short summary from the transcript (heuristic, not LLM)."""
    user_msgs = [t["text"] for t in turns if t["role"] == "user"]
    first = user_msgs[0][:200] if user_msgs else "(no user messages)"
    last = user_msgs[-1][:200] if user_msgs else ""
    return (
        f"Session {session_path}\n"
        f"Turns: {len(turns)} ({sum(1 for t in turns if t['role']=='user')} user, "
        f"{sum(1 for t in turns if t['role']=='assistant')} assistant)\n"
        f"First: {first}\n"
        f"Last: {last}\n"
    )


def capture(
    session_file: Path,
    summary_text: str | None = None,
    dry_run: bool = False,
    pg_url: str = None,
) -> dict:
    """Main entry point. Returns dict with written paths."""
    pg_url = pg_url if pg_url is not None else home_db_url()
    turns = extract_transcript(session_file)
    if not turns:
        return {"error": "no text turns found in session file"}

    datestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    transcript_text = _format_transcript(turns)

    if dry_run:
        slot = f"palace.sessions.{datestamp}-NN"
        print(f"DRY-RUN: would write {slot} and palace.transcripts.{datestamp}-NN")
        print(f"  turns: {len(turns)}")
        print(f"  transcript chars: {len(transcript_text)}")
        return {"dry_run": True, "turns": len(turns)}

    conn = psycopg2.connect(pg_url)
    session_path = _next_session_slot(conn, datestamp)
    n_suffix = session_path.split("-")[-1]
    transcript_path = f"palace.transcripts.{datestamp}-{n_suffix}"

    summary = summary_text or _auto_summary(turns, session_path)
    session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    session_meta = psycopg2.extras.Json(
        {
            "tags": ["session", "rollup"],
            "date": session_date,
            "turn_count": len(turns),
            "transcript_path": transcript_path,
        }
    )
    transcript_meta = psycopg2.extras.Json(
        {
            "tags": ["transcript"],
            "date": session_date,
            "session_path": session_path,
            "source_file": str(session_file),
        }
    )

    with conn.cursor() as cur:
        cur.execute(
            _UPSERT,
            (session_path, f"Session {session_date}", summary, "session", session_meta),
        )
        cur.execute(
            _UPSERT,
            (
                transcript_path,
                f"Transcript {session_date}",
                transcript_text,
                "transcript",
                transcript_meta,
            ),
        )
    conn.commit()
    conn.close()

    # Flat-file echo
    _ECHO_DIR.mkdir(parents=True, exist_ok=True)
    echo_session = _ECHO_DIR / f"{session_path.replace('.', '_')}.md"
    echo_transcript = _ECHO_DIR / f"{transcript_path.replace('.', '_')}.md"
    echo_session.write_text(summary)
    echo_transcript.write_text(transcript_text)

    return {
        "session_path": session_path,
        "transcript_path": transcript_path,
        "turns": len(turns),
        "echo_session": str(echo_session),
        "echo_transcript": str(echo_transcript),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--session-id", help="Session UUID to load from ~/.claude/projects/"
    )
    ap.add_argument("--session-file", help="Explicit path to .jsonl session file")
    ap.add_argument("--summary", help="Summary text for the session node")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.session_file:
        session_file = Path(args.session_file)
    elif args.session_id:
        matches = list(_PROJECTS_DIR.rglob(f"{args.session_id}.jsonl"))
        if not matches:
            print(
                f"ERROR: session {args.session_id} not found under {_PROJECTS_DIR}",
                file=sys.stderr,
            )
            sys.exit(1)
        session_file = matches[0]
    else:
        session_file = _find_latest_session_file()
        if not session_file:
            print(
                f"ERROR: no .jsonl session files found under {_PROJECTS_DIR}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Auto-detected session: {session_file.name}")

    result = capture(session_file, summary_text=args.summary, dry_run=args.dry_run)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
