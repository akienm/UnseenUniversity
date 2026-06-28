#!/usr/bin/env python3
"""
audit_check_sqlite_imports.py — CLAUDE.md hard rule enforcement.

Greps devices/igor/ for `import sqlite3` and `from sqlite3 import ...`,
excluding files that legitimately wrap EXTERNAL SQLite stores (Calibre's
catalog, Kindle DRM keychain, etc.). Igor's OWN data is Postgres only.

Empty stdout = pass. Non-empty = list of violations, one per line:
  devices/igor/path/file.py:LINE: matched line

Tracked separately from T-remove-sqlite-references — that ticket is the
cleanup; this check prevents reintroduction.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "unseen_university" / "devices" / "igor"

# Files that legitimately import sqlite3 because they read EXTERNAL
# SQLite stores Igor doesn't own. NOT exemptions for Igor's own data.
LEGITIMATE_EXTERNAL = {
    "tools/ebook_reader.py",  # Calibre catalog (external)
    "tools/learner.py",  # Calibre metadata.db read (external — Calibre's own store)
    "tools/ebook_drm/androidkindlekey.py",  # Android Kindle keychain (external)
}

PATTERN = re.compile(r"^\s*(import sqlite3|from sqlite3 )")


def _is_external(rel_path: str) -> bool:
    return rel_path in LEGITIMATE_EXTERNAL


def main() -> int:
    if not SOURCE_ROOT.exists():
        print(f"source root not found: {SOURCE_ROOT}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        rel = str(path.relative_to(SOURCE_ROOT))
        if _is_external(rel):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if PATTERN.match(line):
                full_rel = path.relative_to(REPO_ROOT)
                violations.append(f"{full_rel}:{lineno}: {line.strip()}")

    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
