#!/usr/bin/env python3
"""claude_md_anchor_check — every file-path anchor in CLAUDE.md resolves in the tree.

CLAUDE.md is the bootstrap doc; a path it names that no longer resolves teaches a
fresh builder a lie (rebuildability-diff F3: it said diagnostic_base/core_values.py
after the single-package reorg moved the file under unseen_university/). This scans
CLAUDE.md for backtick-wrapped file paths (a `/` and a known extension) and checks
each against the repo root.

Prose shorthands (a bare filename with no directory, e.g. `core_values.py`,
`__init__.py`) and paths explicitly marked retired/old in their line are NOT
anchors — they are excluded, so the check flags only paths that CLAIM to resolve.

Run: python3 devlab/claudecode/claude_md_anchor_check.py
Exit 0 iff every resolvable-claimed anchor resolves.
"""
from __future__ import annotations

import os
import re
import sys

# devlab/claudecode/claude_md_anchor_check.py -> repo root is three dirs up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Backtick-wrapped token containing a slash and ending in a known file extension.
_PATH_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*\.(?:py|md|json|txt|yaml|yml|cfg|sh|service))`"
)
# Lines describing a retired/historical artifact don't assert a live path.
_HISTORICAL = ("retired", "old ", "deprecated", "removed", "predates", "legacy", "was ")


def collect_anchors(md_path: str) -> list[tuple[int, str]]:
    anchors = []
    with open(md_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            low = line.lower()
            for m in _PATH_RE.finditer(line):
                path = m.group(1)
                # Retired reference on this line -> not a live anchor.
                if any(h in low for h in _HISTORICAL):
                    continue
                anchors.append((lineno, path))
    return anchors


def main() -> int:
    md_path = os.path.join(_REPO, "CLAUDE.md")
    anchors = collect_anchors(md_path)
    stale = []
    for lineno, path in sorted(set(anchors)):
        ok = os.path.exists(os.path.join(_REPO, path))
        print(f"{'ok ' if ok else 'STALE'} CLAUDE.md:{lineno}  {path}")
        if not ok:
            stale.append((lineno, path))
    print(f"\nanchors={len(set(anchors))} stale={len(stale)}")
    for lineno, path in stale:
        print(f"  STALE CLAUDE.md:{lineno}  {path}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
