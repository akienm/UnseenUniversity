#!/usr/bin/env python3
"""rotate_logs.py — rotate operational logs in ~/.unseen_university/logs/ when >10MB.

Keeps at most one backup (.log.1). Safe to call repeatedly (idempotent when
files are small). Called by day-close-audit Step 7 so manual truncation
between day-close runs is never needed.

Usage:
    python3 lab/claudecode/rotate_logs.py [--dry-run]
    python3 lab/claudecode/rotate_logs.py --max-mb 20   # override threshold
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


_LOG_ROOT = Path(os.environ.get("IGOR_HOME", str(Path.home() / ".unseen_university"))) / "logs"
_DEFAULT_MAX_MB = 10


def rotate_logs(log_root: Path = _LOG_ROOT, max_mb: int = _DEFAULT_MAX_MB, dry_run: bool = False) -> list[str]:
    """Rotate any .log file under log_root that exceeds max_mb.

    Returns list of rotated file paths.
    """
    max_bytes = max_mb * 1024 * 1024
    rotated: list[str] = []

    if not log_root.exists():
        return rotated

    for log_file in sorted(log_root.rglob("*.log")):
        if not log_file.is_file():
            continue
        size = log_file.stat().st_size
        if size <= max_bytes:
            continue

        backup = log_file.with_suffix(".log.1")
        if dry_run:
            print(f"[rotate-logs] would rotate {log_file} ({size // (1024*1024)}MB → .1)")
        else:
            if backup.exists():
                backup.unlink()
            log_file.rename(backup)
            log_file.touch()
            print(f"[rotate-logs] rotated {log_file} ({size // (1024*1024)}MB → .1)")
        rotated.append(str(log_file))

    return rotated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-mb", type=int, default=_DEFAULT_MAX_MB)
    parser.add_argument("--log-root", type=Path, default=_LOG_ROOT)
    args = parser.parse_args()

    rotated = rotate_logs(log_root=args.log_root, max_mb=args.max_mb, dry_run=args.dry_run)
    if not rotated:
        print(f"[rotate-logs] all logs under {args.log_root} are ≤{args.max_mb}MB — nothing to do")


if __name__ == "__main__":
    main()
