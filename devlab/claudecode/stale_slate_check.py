#!/usr/bin/env python3
"""stale_slate_check.py — soft-prompt /day-close when the most recent slate is stale.

Called by /context-load (Step 0.25) at session start. If the most recent prior-day
slate in ~/.TheIgors/claudecode/ has open items (non-empty Next up / Blocked /
After that sections) and lacks a ✅ CLOSED marker, emit a soft prompt telling the
user their previous day hasn't been closed. Silent when the prior slate is fully
closed or empty.

Exit code is always 0 — this is a prompt, not a gate.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

SLATE_DIR = (
    Path(
        __import__("os").environ.get(
            "IGOR_HOME", str(Path.home() / ".unseen_university")
        )
    )
    / "claudecode"
)
SLATE_RE = re.compile(r"^(\d{8})\.slate\.txt$")
CLOSED_MARKER = "✅ CLOSED"
OPEN_SECTION_HEADINGS = ("## Next up", "## Blocked", "## After that")
DAYCLOSE_MARKER_PREFIX = "## Day-close for "


def find_latest_slate_before(today: date, slate_dir: Path = SLATE_DIR) -> Path | None:
    """Return the newest YYYYMMDD.slate.txt older than `today`, or None."""
    if not slate_dir.exists():
        return None
    candidates: list[tuple[date, Path]] = []
    for p in slate_dir.glob("*.slate.txt"):
        m = SLATE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if d < today:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def slate_has_open_items(path: Path) -> bool:
    """True when the slate has open items and is not marked closed.

    Handles both JSON (new) and markdown (old) slate formats.
    JSON: open when in_flight or planned is non-empty and closed != true.
    Markdown: open when ## Next up / Blocked / After that sections have content
    and the ✅ CLOSED marker is absent.
    """
    import json as _json

    text = path.read_text(encoding="utf-8")

    # Try JSON format first
    try:
        data = _json.loads(text)
        if data.get("closed"):
            return False
        return bool(data.get("in_flight") or data.get("planned"))
    except (ValueError, AttributeError):
        pass

    # Markdown format (backward compat)
    if CLOSED_MARKER in text:
        return False
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        heading = lines[i].rstrip()
        if heading in OPEN_SECTION_HEADINGS:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                body = lines[j].strip()
                if body and not body.startswith("#"):
                    return True
                j += 1
            i = j
        else:
            i += 1
    return False


def format_slate_date(filename: str) -> str:
    return f"{filename[:4]}-{filename[4:6]}-{filename[6:8]}"


def today_slate_has_dayclose_for(
    closing_date: str, slate_dir: Path = SLATE_DIR
) -> bool:
    """True when today's slate contains a Day-close completion marker for closing_date (YYYY-MM-DD)."""
    today_path = slate_dir / f"{date.today().strftime('%Y%m%d')}.slate.txt"
    if not today_path.exists():
        return False
    return f"{DAYCLOSE_MARKER_PREFIX}{closing_date}: complete" in today_path.read_text()


def main() -> int:
    today = date.today()
    slate = find_latest_slate_before(today)
    if slate is None:
        return 0
    slate_date = format_slate_date(slate.name)
    if today_slate_has_dayclose_for(slate_date):
        print(f"✓ day-close for {slate_date}: already complete")
        return 0
    if not slate_has_open_items(slate):
        return 0
    print(f"⚠ stale slate: {slate_date} has open items and no ✅ CLOSED marker")
    print(
        f"  run /day-close for {slate_date} before starting new work, or confirm skip"
    )
    print(f"  path: {slate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
