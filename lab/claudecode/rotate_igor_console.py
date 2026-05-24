#!/usr/bin/env python3
"""
rotate_igor_console.py — T-igor-console-midnight-rotate

Re-point Igor's tmux pipe-pane to today's console log file. The igor
launcher (~/bin/igor → /home/akien/TheIgors/igor) starts pipe-pane to
$ADC_HOME/logs/Igor-wild-0001/YYYYMMDD.console.log AT SESSION START ONLY.
Long-lived sessions keep appending to the launch-day file. This script
rotates that pipe at day boundaries so each calendar day gets its own
file.

Cron entry (Akien adds manually):
    1 0 * * * /home/akien/TheIgors/venv/bin/python \\
        /home/akien/TheIgors/lab/claudecode/rotate_igor_console.py \\
        >> /home/akien/.TheIgors/logs/console_rotate.log 2>&1

Idempotent: re-running mid-day re-points to today's file (already correct);
no harm done. Exits cleanly when the tmux session doesn't exist.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _adc_home() -> Path:
    return Path(
        os.environ.get("UNSEEN_UNIVERSITY_HOME", str(Path.home() / ".unseen_university"))
    )


def _session_name() -> str:
    return os.environ.get("IGOR_TMUX_SESSION", "igor")


def target_log_path(now: datetime, adc_home: Path | None = None) -> Path:
    """Compute the expected console log path for the given date."""
    home = adc_home or _adc_home()
    return home / "logs" / "Igor-wild-0001" / f"{now.strftime('%Y%m%d')}.console.log"


def tmux_session_exists(session: str) -> bool:
    """True if the named tmux session currently exists."""
    if shutil.which("tmux") is None:
        return False
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def rotate(now: datetime | None = None, dry_run: bool = False) -> int:
    """Rotate Igor's tmux pipe-pane to today's log file.

    Returns 0 on success, 1 if no tmux session, 2 on tmux command failure.
    """
    now = now or datetime.now()
    session = _session_name()
    log_path = target_log_path(now)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not tmux_session_exists(session):
        print(f"[rotate-console] no tmux session '{session}', nothing to do")
        return 1

    pipe_cmd = f"cat >> {str(log_path)!r}"

    if dry_run:
        print(f"[rotate-console] would re-pipe '{session}' to {log_path}")
        return 0

    # Close any existing pipe (no-op when none active)
    close_result = subprocess.run(
        ["tmux", "pipe-pane", "-t", session],
        capture_output=True,
        text=True,
    )
    if close_result.returncode != 0:
        print(
            f"[rotate-console] tmux pipe-pane close failed: {close_result.stderr.strip()}"
        )
        return 2

    # Open a new pipe to today's file
    open_result = subprocess.run(
        ["tmux", "pipe-pane", "-t", session, "-o", pipe_cmd],
        capture_output=True,
        text=True,
    )
    if open_result.returncode != 0:
        print(
            f"[rotate-console] tmux pipe-pane open failed: {open_result.stderr.strip()}"
        )
        return 2

    print(f"[rotate-console] {now.isoformat()} pipe-pane → {log_path}")
    return 0


def main() -> int:
    dry = "--dry-run" in sys.argv
    return rotate(dry_run=dry)


if __name__ == "__main__":
    sys.exit(main())
