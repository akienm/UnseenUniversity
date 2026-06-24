#!/usr/bin/env python3
"""
Record actual token consumption for a sprint to sprint_tokens.log.

Reads the current CC session transcript (most-recently-modified .jsonl),
sums input_tokens + cache_creation_input_tokens + cache_read_input_tokens
and output_tokens for all assistant turns since sprint_start, appends
one line to $IGOR_HOME/claudecode/sprint_tokens.log.

Usage:
    sprint_token_log.py <ticket_id> <sprint_start_iso>

sprint_start_iso: ISO timestamp recorded when the ticket went in_progress,
e.g. "2026-06-04T03:15:00.000Z". All assistant turns at or after this
timestamp are counted.

Log format (pipe-delimited, append-only):
    timestamp|ticket_id|input_tokens|cache_create|cache_read|output_tokens|model
"""
from __future__ import annotations
from unseen_university._uu_root import uu_home

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _find_current_transcript() -> Path | None:
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    transcripts = sorted(
        projects_dir.rglob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return transcripts[0] if transcripts else None


def _sum_tokens(transcript: Path, sprint_start: str) -> dict:
    totals = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "turns": 0,
    }
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue
                ts = entry.get("timestamp", "")
                if ts < sprint_start:
                    continue

                usage = entry.get("message", {}).get("usage", {})
                if not usage:
                    continue

                totals["input_tokens"] += usage.get("input_tokens", 0)
                totals["cache_creation_input_tokens"] += usage.get(
                    "cache_creation_input_tokens", 0
                )
                totals["cache_read_input_tokens"] += usage.get(
                    "cache_read_input_tokens", 0
                )
                totals["output_tokens"] += usage.get("output_tokens", 0)
                totals["turns"] += 1
    except OSError as e:
        print(f"sprint_token_log: transcript read error: {e}", file=sys.stderr)
    return totals


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: sprint_token_log.py <ticket_id> <sprint_start_iso>",
            file=sys.stderr,
        )
        sys.exit(1)

    ticket_id = sys.argv[1]
    sprint_start = sys.argv[2]

    transcript = _find_current_transcript()
    if not transcript:
        print(
            f"sprint_token_log: no transcript found — skipping for {ticket_id}",
            file=sys.stderr,
        )
        return

    totals = _sum_tokens(transcript, sprint_start)

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log_path = (
        Path(uu_home())
        / "claudecode"
        / "sprint_tokens.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    line = (
        f"{ts}|{ticket_id}"
        f"|{totals['input_tokens']}"
        f"|{totals['cache_creation_input_tokens']}"
        f"|{totals['cache_read_input_tokens']}"
        f"|{totals['output_tokens']}"
        f"|{model}\n"
    )
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)

    total_in = (
        totals["input_tokens"]
        + totals["cache_creation_input_tokens"]
        + totals["cache_read_input_tokens"]
    )
    print(
        f"Token log: {ticket_id} — "
        f"{total_in} in ({totals['cache_creation_input_tokens']} cache-write, "
        f"{totals['cache_read_input_tokens']} cache-read) / "
        f"{totals['output_tokens']} out "
        f"({totals['turns']} turns)"
    )


if __name__ == "__main__":
    main()
