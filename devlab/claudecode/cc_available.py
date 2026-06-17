#!/usr/bin/env python3
"""
cc_available.py — the availability "button" CC presses to rejoin Granny dispatch.

When CC steps away to do something else (an interactive session, a long manual
task), Granny may have marked CC.0 unavailable — either because CC set .false, or
because a bus dispatch timed out and Granny dropped a cooldown. This is the
self-serve reset: one command that clears the block and opts CC back in.

Self-contained on purpose — it manipulates the flag files directly rather than
importing devices.granny.availability, so the button works from any cwd with no
PYTHONPATH setup. The protocol it mirrors:

    ~/.granny/available/{worker}.available.true   — opted in
    ~/.granny/available/{worker}.available.false  — blocked (.false wins)
    ~/.granny/available/{worker}.cooldown_until    — epoch expiry of a Granny cooldown

Usage:
    cc_available.py on  [worker]   # reset: clear .false + cooldown, set .true   (default)
    cc_available.py off [worker]   # step away: set .false (no cooldown)
    cc_available.py status [worker]

Default worker is CC.0. Override the dir with GRANNY_AVAIL_DIR.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

DEFAULT_WORKER = "CC.0"


def _avail_dir() -> Path:
    d = Path(os.environ.get("GRANNY_AVAIL_DIR", str(Path.home() / ".granny" / "available")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths(worker: str) -> tuple[Path, Path, Path]:
    d = _avail_dir()
    return (
        d / f"{worker}.available.true",
        d / f"{worker}.available.false",
        d / f"{worker}.cooldown_until",
    )


def turn_on(worker: str) -> None:
    """Full reset to available: clear .false AND cooldown_until, set .true.

    Clearing cooldown_until matters — mark_available alone leaves a stale future
    cooldown file that just churns; the button is meant to fully rejoin dispatch.
    """
    true_f, false_f, cooldown_f = _paths(worker)
    false_f.unlink(missing_ok=True)
    cooldown_f.unlink(missing_ok=True)
    true_f.touch()
    print(f"availability: {worker} → AVAILABLE (.true set, .false + cooldown cleared)")


def turn_off(worker: str) -> None:
    """Step away: set .false, clear .true. No cooldown (manual, not a timeout)."""
    true_f, false_f, _cooldown_f = _paths(worker)
    true_f.unlink(missing_ok=True)
    false_f.touch()
    print(f"availability: {worker} → UNAVAILABLE (.false set)")


def status(worker: str) -> None:
    true_f, false_f, cooldown_f = _paths(worker)
    if false_f.exists():
        state = "UNAVAILABLE (.false present — wins over .true)"
    elif true_f.exists():
        state = "AVAILABLE (.true present)"
    else:
        state = "UNAVAILABLE (no .true flag)"
    cd = ""
    if cooldown_f.exists():
        try:
            expiry = float(cooldown_f.read_text().strip())
            remaining = expiry - time.time()
            cd = (
                f"; cooldown_until in {remaining:.0f}s"
                if remaining > 0
                else "; cooldown_until expired (will clear on next Granny poll)"
            )
        except (ValueError, OSError):
            cd = "; cooldown_until present (unparseable)"
    print(f"availability: {worker} — {state}{cd}")


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "on"
    worker = argv[2] if len(argv) > 2 else DEFAULT_WORKER
    if action in ("on", "available", "reset"):
        turn_on(worker)
    elif action in ("off", "unavailable", "away"):
        turn_off(worker)
    elif action in ("status", "show"):
        status(worker)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
