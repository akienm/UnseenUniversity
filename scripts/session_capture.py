#!/usr/bin/env python3
"""session_capture.py — Capture the current CC session as a flat-file session node.

Reads the session JSONL, strips tool calls/results/thinking blocks, and writes a
single session record (summary + text-only transcript) into the canonical
filesystem memory store at ``devlab/runtime/memory/sessions/`` via the
``memory_emit`` chokepoint. NO Postgres driver: the retired palace table is the
dead store (D-canonical-memory-consolidation) — this script used to INSERT into
it on every session close, a dead interface crossing. The flat-file store IS the
durable home now (no separate echo dir needed).

Usage:
    python3 scripts/session_capture.py                        # auto-detect current session
    python3 scripts/session_capture.py --session-id <id>      # explicit session JSONL id
    python3 scripts/session_capture.py --session-file <path>  # explicit JSONL path
    python3 scripts/session_capture.py --summary <text>       # summary text (else auto-drafted)
    python3 scripts/session_capture.py --dry-run              # print without writing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from unseen_university._uu_root import uu_root

# memory_emit.py is the single write chokepoint for the filesystem memory store.
# It lives in devlab/claudecode/ (dev tooling), not in the package — put it on the
# path via the canonical repo-root resolver.
sys.path.insert(0, str(Path(uu_root()) / "devlab" / "claudecode"))
from memory_emit import emit  # noqa: E402

log = logging.getLogger(__name__)

_PROJECTS_DIR = Path.home() / ".claude" / "projects"


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
    emit_fn=emit,
) -> dict:
    """Capture the session as a flat-file node in devlab/runtime/memory/sessions/.

    Returns a dict with the written node path (or dry-run / error info). No
    Postgres connection is made — the canonical filesystem store is the home.
    """
    turns = extract_transcript(session_file)
    if not turns:
        return {"error": "no text turns found in session file"}

    session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    datestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    transcript_text = _format_transcript(turns)
    summary = summary_text or _auto_summary(turns, f"session-{datestamp}")

    if dry_run:
        print(
            "DRY-RUN: would emit a session node to devlab/runtime/memory/sessions/ "
            f"(turns={len(turns)}, transcript chars={len(transcript_text)})"
        )
        return {"dry_run": True, "turns": len(turns)}

    body = {
        "title": f"Session {session_date}",
        "date": session_date,
        "turn_count": len(turns),
        "summary": summary,
        "transcript": transcript_text,
        "source_file": str(session_file),
        "tags": ["session", "rollup"],
        # `text` is the grep-readable field every store reader surfaces.
        "text": summary,
    }
    # Interface crossing (session write to the memory store) — log at INFO.
    node_path = emit_fn(
        "sessions", "cc.0", body, kind="session", namespace=f"session-{datestamp}"
    )
    log.info(
        "session_capture: wrote session node %s (turns=%d)", node_path, len(turns)
    )
    return {"session_node": node_path, "turns": len(turns)}


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
