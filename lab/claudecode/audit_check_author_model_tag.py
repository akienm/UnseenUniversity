#!/usr/bin/env python3
"""
audit_check_author_model_tag.py — T-author-model-header-on-new-files

# author-model: opus

Companion to T-blame-with-model. Blame surfaces who edited each line; this
check enforces a tag on NEW files so provenance survives later rewrites
(blame loses the original author once a file gets fully rewritten).

Convention: a new Python file includes a tag line near the top:

    # author-model: opus
    # author-model: sonnet
    # author-model: haiku
    # author-model: human
    # author-model: igor

Any of these tokens count. The tag may live in the module docstring or as
a top-of-file comment; case-insensitive.

This check scans new files added in a diff range (default: last commit
vs HEAD~1) and reports any new .py file under lab/utility_closet/,
or lab/claudecode/ that lacks the tag. Exempt: tests/ and __init__.py.

Empty stdout = pass. Non-empty = list of violations, one per line.

Usage:
    audit_check_author_model_tag.py                 # diff HEAD~1..HEAD
    audit_check_author_model_tag.py --range A..B    # custom range
    audit_check_author_model_tag.py --staged        # files newly added in stage

Returns 0 on pass, 1 on violations, 2 on usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories where the tag is enforced
ENFORCED_DIRS = (
    Path("/home/akien/dev/src/UnseenUniversity") / "devices" / "igor",
    REPO_ROOT / "lab" / "utility_closet",
    REPO_ROOT / "lab" / "claudecode",
)

# Files exempt from the rule
EXEMPT_NAMES = {"__init__.py"}

# Recognized tokens — case-insensitive substring match on the tag line
RECOGNIZED_MODELS = ("opus", "sonnet", "haiku", "human", "igor", "akien")

# Tag pattern: "author-model: <token>" anywhere in the first ~30 lines.
# Tolerates leading "#" or being inside a docstring.
TAG_RE = re.compile(r"author-model\s*:\s*([A-Za-z0-9._-]+)", re.IGNORECASE)

# Only scan the top of the file (header convention)
HEADER_LINE_LIMIT = 30


def is_enforced_path(path: Path) -> bool:
    """True if `path` is a .py file under an enforced directory and not exempt."""
    if path.suffix != ".py":
        return False
    if path.name in EXEMPT_NAMES:
        return False
    if "tests" in path.parts:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in ENFORCED_DIRS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def has_author_model_tag(content: str) -> tuple[bool, str]:
    """Return (has_tag, model_token). Scans only the top HEADER_LINE_LIMIT lines."""
    head = "\n".join(content.splitlines()[:HEADER_LINE_LIMIT])
    m = TAG_RE.search(head)
    if not m:
        return False, ""
    token = m.group(1).strip().lower()
    return True, token


def is_recognized_token(token: str) -> bool:
    """True if the tag token matches one of the recognized models."""
    if not token:
        return False
    low = token.lower()
    return any(t in low for t in RECOGNIZED_MODELS)


def get_new_files_in_range(diff_range: str) -> list[Path]:
    """Return new .py files (added/created) in the given git diff range."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "diff",
            "--name-only",
            "--diff-filter=A",
            diff_range,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    out: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(REPO_ROOT / line)
    return out


def get_staged_new_files() -> list[Path]:
    """Return .py files newly added in the staging area."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=A",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    out: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(REPO_ROOT / line)
    return out


def check_files(paths: list[Path]) -> list[str]:
    """Return list of violation messages (empty = pass)."""
    violations: list[str] = []
    for path in paths:
        if not is_enforced_path(path):
            continue
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        has_tag, token = has_author_model_tag(content)
        rel = path.resolve().relative_to(REPO_ROOT)
        if not has_tag:
            violations.append(f"{rel}: missing 'author-model:' tag in header")
        elif not is_recognized_token(token):
            violations.append(
                f"{rel}: tag value {token!r} not recognized "
                f"(allowed: {', '.join(RECOGNIZED_MODELS)})"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--range",
        default="HEAD~1..HEAD",
        help="git diff range to scan (default HEAD~1..HEAD)",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan staged additions instead of a commit range",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Specific files to check (overrides --range and --staged)",
    )
    args = parser.parse_args(argv)

    if args.files:
        paths = [p if p.is_absolute() else REPO_ROOT / p for p in args.files]
    elif args.staged:
        paths = get_staged_new_files()
    else:
        paths = get_new_files_in_range(args.range)

    violations = check_files(paths)
    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
