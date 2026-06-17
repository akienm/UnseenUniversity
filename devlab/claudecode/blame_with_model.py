#!/usr/bin/env python3
"""
blame_with_model.py — git blame enriched with Co-Authored-By model attribution.
T-blame-with-model.

Multi-model debugging requires knowing which model authored a given chunk
of code. Co-Authored-By trailers already capture this in commit messages
(e.g. "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>").
This script runs git blame on a file (or a line range), looks up each
commit's Co-Authored-By trailer, and outputs blame with model attribution.

Usage:
    blame_with_model.py <file> [start_line[:end_line]]
    blame_with_model.py <file> --model-only opus
    blame_with_model.py <file> --json

Output (default):
    <line>  <commit-short>  <author>  <model>  <code>

Output (--json):
    [{"line": N, "commit": "...", "author": "...", "model": "...", "code": "..."}, ...]

Notes:
  - Lines without a Co-Authored-By trailer show model="-".
  - --model-only filters to lines whose model name (case-insensitive)
    contains the given substring (e.g. "opus", "sonnet", "haiku").
  - Caches git log results per commit so a long file doesn't pay N×git-log.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Patterns ─────────────────────────────────────────────────────────────────

# git blame --line-porcelain emits one stanza per line. The first line is the
# commit SHA and line numbers, then header lines (author, summary, ...),
# then a single "\t<source>" line.
_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")

# Co-Authored-By trailer (case-insensitive)
_COAUTHOR = re.compile(
    r"^[Cc]o-[Aa]uthored-[Bb]y:\s*(?P<name>.+?)\s*<(?P<email>[^>]+)>\s*$"
)

# Try to extract a model "family" name from the Co-Authored-By value, e.g.
# "Claude Opus 4.7 (1M context)" -> "opus". Falls back to the full name.
_MODEL_TOKENS = ("opus", "sonnet", "haiku")


# ── Data shapes ──────────────────────────────────────────────────────────────


@dataclass
class BlameLine:
    line: int
    commit: str  # full sha
    author: str
    model: str  # short token if extractable, else full coauthor or "-"
    code: str

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "commit": self.commit[:8],
            "author": self.author,
            "model": self.model,
            "code": self.code,
        }


# ── git wrappers ─────────────────────────────────────────────────────────────


def _run(args: list[str], cwd: Optional[Path] = None) -> str:
    """Run a command and return stdout. Returns "" on non-zero exit."""
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=str(cwd) if cwd else None
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def get_commit_message(sha: str, cwd: Optional[Path] = None) -> str:
    """Return the full commit message (subject + body) for a sha."""
    return _run(["git", "log", "-1", "--format=%B", sha], cwd=cwd)


def extract_coauthor_model(commit_message: str) -> tuple[str, str]:
    """Return (coauthor_name, model_token) from the message's Co-Authored-By trailer.

    model_token is one of "opus" | "sonnet" | "haiku" if recognized in the
    coauthor name, otherwise the full coauthor name (or "-" if no trailer).
    Multiple trailers: returns the first.
    """
    for line in commit_message.splitlines():
        m = _COAUTHOR.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        lower = name.lower()
        for token in _MODEL_TOKENS:
            if token in lower:
                return name, token
        return name, name
    return "", "-"


# ── blame parsing ────────────────────────────────────────────────────────────


def parse_blame_porcelain(blame_output: str) -> list[tuple[int, str, str, str]]:
    """Parse `git blame --line-porcelain` output.

    Returns list of (line_number, sha, author, source_text).
    """
    out: list[tuple[int, str, str, str]] = []
    cur_sha: Optional[str] = None
    cur_line: Optional[int] = None
    cur_author: str = ""
    for raw in blame_output.splitlines():
        m = _BLAME_HEADER.match(raw)
        if m:
            cur_sha = m.group(1)
            cur_line = int(m.group(3))
            cur_author = ""
            continue
        if raw.startswith("author "):
            cur_author = raw[len("author ") :]
            continue
        if raw.startswith("\t") and cur_sha and cur_line is not None:
            source = raw[1:]
            out.append((cur_line, cur_sha, cur_author, source))
            cur_sha = None
            cur_line = None
    return out


def blame_file(
    path: Path,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    cwd: Optional[Path] = None,
) -> list[BlameLine]:
    """Run blame on path (optionally a line range) and enrich with model attribution."""
    args = ["git", "blame", "--line-porcelain"]
    if start_line is not None:
        end = end_line if end_line is not None else start_line
        args.extend(["-L", f"{start_line},{end}"])
    args.extend(["--", str(path)])

    raw = _run(args, cwd=cwd)
    parsed = parse_blame_porcelain(raw)

    # Cache messages per commit so we don't run N git-log calls
    msg_cache: dict[str, str] = {}
    out: list[BlameLine] = []
    for line_num, sha, author, source in parsed:
        if sha not in msg_cache:
            msg_cache[sha] = get_commit_message(sha, cwd=cwd)
        _coauthor_name, model = extract_coauthor_model(msg_cache[sha])
        out.append(
            BlameLine(
                line=line_num,
                commit=sha,
                author=author,
                model=model,
                code=source,
            )
        )
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────


def _format_line(b: BlameLine) -> str:
    return f"{b.line:5d}  {b.commit[:8]}  {b.author:20s}  {b.model:10s}  {b.code}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="File to blame")
    parser.add_argument(
        "range",
        nargs="?",
        default=None,
        help="Line range — N or N:M (1-indexed, inclusive)",
    )
    parser.add_argument(
        "--model-only",
        default=None,
        help="Filter to lines whose model contains this substring (case-insensitive)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)

    start = end = None
    if args.range:
        if ":" in args.range:
            s, e = args.range.split(":", 1)
            start, end = int(s), int(e)
        else:
            start = int(args.range)
            end = start

    blames = blame_file(args.file, start_line=start, end_line=end)

    if args.model_only:
        needle = args.model_only.lower()
        blames = [b for b in blames if needle in b.model.lower()]

    if args.json:
        print(json.dumps([b.to_dict() for b in blames], indent=2))
    else:
        for b in blames:
            print(_format_line(b))

    return 0


if __name__ == "__main__":
    sys.exit(main())
