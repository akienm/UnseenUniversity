#!/usr/bin/env python3
"""
audit_check_bare_except.py — silent-except detector.

Walks devices/igor/ for `except: pass` blocks (single bare-except whose only
body statement is `pass`). These silently swallow errors and are forbidden by
TheIgors coding rules.

Empty stdout = pass. Non-empty = list of violations, one per line:
  devices/igor/path/file.py:LINE
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path("/home/akien/dev/src/UnseenUniversity") / "devices" / "igor"

# Skip third-party DRM / vendored code
SKIP_DIRS = ("ebook_drm",)


def _check_file(path: Path) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Bare except (no type) with single-pass body
        if (
            node.type is None
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ):
            rel = path.relative_to(REPO_ROOT)
            out.append(f"{rel}:{node.lineno}")
    return out


def main() -> int:
    if not SOURCE_ROOT.exists():
        print(f"source root not found: {SOURCE_ROOT}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        if any(d in str(path) for d in SKIP_DIRS):
            continue
        violations.extend(_check_file(path))

    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
