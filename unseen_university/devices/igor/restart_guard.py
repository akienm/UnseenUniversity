"""restart_guard.py — T-daemon-supervisor-backoff-and-one-shot-audit.

Rate-limit restart.flag triggers so a critical-thread death in a tight
boot-fail-restart loop doesn't burn forever. If more than MAX_RESTARTS
fire within WINDOW_SECS, write a halt flag and refuse further restarts
until the human clears it.

History lives at <instance_dir>/restart_history.json (append-only JSON
array of epoch timestamps, pruned to the current window on each check).
Halt marker lives at <instance_dir>/restart_halt.flag (touched file with
short diagnostic text; existence is the signal).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

HISTORY_FILENAME = "restart_history.json"
HALT_FLAG_FILENAME = "restart_halt.flag"


def record_and_check(
    instance_dir: Path,
    now: float | None = None,
    max_restarts: int = 5,
    window_secs: int = 600,
) -> tuple[bool, int]:
    """Record a restart event at `now` and decide whether to halt.

    Returns (should_halt, restart_count_in_window). When should_halt is
    True, the caller should write the halt flag and exit 0 instead of 42.
    When False, the caller proceeds with the normal restart.

    Never raises — file I/O errors fall back to a fresh history so a
    single write failure doesn't wedge the halt path.
    """
    if now is None:
        now = time.time()
    history_path = instance_dir / HISTORY_FILENAME
    history: list[float] = []
    if history_path.exists():
        try:
            loaded = json.loads(history_path.read_text())
            if isinstance(loaded, list):
                history = [float(t) for t in loaded if isinstance(t, (int, float))]
        except Exception as e:
            log.debug("record_and_check: json.loads failed: %s", e)
    # Keep only entries within the window
    history = [t for t in history if (now - t) <= window_secs]
    history.append(now)
    try:
        history_path.write_text(json.dumps(history))
    except Exception as e:
        log.debug("record_and_check: write_text failed: %s", e)
    return len(history) > max_restarts, len(history)


def write_halt(instance_dir: Path, count: int, window_secs: int) -> None:
    """Write the halt marker. Silent on I/O error — caller will still exit."""
    halt_path = instance_dir / HALT_FLAG_FILENAME
    try:
        halt_path.write_text(
            f"halted at {time.time()}: {count} restarts in {window_secs}s"
        )
    except Exception as e:
        log.debug("write_halt: write_text failed: %s", e)


def halt_present(instance_dir: Path) -> bool:
    return (instance_dir / HALT_FLAG_FILENAME).exists()


def clear_history(instance_dir: Path) -> None:
    """Reset the history file (useful after manually clearing halt flag)."""
    path = instance_dir / HISTORY_FILENAME
    if path.exists():
        try:
            path.unlink()
        except Exception as e:
            log.debug("clear_history: unlink failed: %s", e)
