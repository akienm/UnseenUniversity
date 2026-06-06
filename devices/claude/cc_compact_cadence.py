#!/usr/bin/env python3
# author-model: sonnet
"""
cc_compact_cadence.py — Stop hook: fire /autocompact every N ticket-closes.

Why a hook and not a skill line: the "compact every N tickets" rule used to
live only in /sprint-batch, which Granny bypasses by dispatching atomic
/sprint-ticket commands. And when the compact line lives inside a skill the
model reads, the model treats it as advisory and defers it. A Stop hook is
harness-enforced — it runs after every CC turn regardless of dispatch path,
and a /autocompact it injects via tmux is a queued slash command the model
cannot defer (it runs as the next turn).

Counter signal: sprint_tokens.log gains exactly one line per ticket-close
(written by sprint_token_log.py in /sprint-ticket step 11). We compare its
current line count to a baseline file; when the delta reaches COMPACT_EVERY_N
we inject /autocompact and reset the baseline to the current count. The
baseline is external state on disk (shim-owned), so the count survives across
turns and sessions and never re-fires on the compaction turn itself (that turn
closes no ticket, so the delta stays 0).

Registered as a Stop hook by ClaudeShim.start(). Safe to run manually; always
exits 0 so it can never block a turn.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# Resolve repo root so `python3 -m devices.claude.cc_compact_cadence` works and
# direct execution also imports the constants module.
_UU_ROOT = Path(__file__).resolve().parents[2]
if str(_UU_ROOT) not in sys.path:
    sys.path.insert(0, str(_UU_ROOT))

from devices.claude.constants import (  # noqa: E402
    COMPACT_EVERY_N,
    TMUX_SESSION,
    compact_baseline_path,
    sprint_tokens_log_path,
)


def count_closes(log_path: Path) -> int:
    """Number of ticket-closes = non-empty lines in sprint_tokens.log (0 if absent)."""
    try:
        return sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip())
    except FileNotFoundError:
        return 0
    except OSError:
        return 0


def read_baseline(baseline_path: Path) -> int:
    """Close-count recorded at the last compaction (0 if absent or unparseable)."""
    try:
        return int(baseline_path.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, ValueError, OSError):
        return 0


def write_baseline(baseline_path: Path, n: int) -> None:
    """Atomically record the close-count at this compaction."""
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = baseline_path.with_suffix(baseline_path.suffix + ".tmp")
    tmp.write_text(str(n), encoding="utf-8")
    tmp.replace(baseline_path)


def should_compact(current: int, baseline: int, every_n: int) -> bool:
    """True when at least every_n closes have happened since the last compaction."""
    return every_n > 0 and (current - baseline) >= every_n


def _tmux_session_exists(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session], capture_output=True
    ).returncode == 0


def inject_autocompact(session: str) -> None:
    """Queue /autocompact into the CC tmux session.

    Mirrors the /autocompact tmux pattern: three Enter signals first (a visible
    interruption that breaks any in-progress input), then the command, then
    Enter to submit. /autocompact itself owns HOW the compaction runs (the
    Haiku dance) — this hook only decides WHEN.
    """
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter", "Enter", "Enter"], check=False)
    time.sleep(0.5)
    subprocess.run(["tmux", "send-keys", "-t", session, "/autocompact"], check=False)
    time.sleep(0.5)
    subprocess.run(["tmux", "send-keys", "-t", session, "ENTER"], check=False)


def main() -> int:
    log_path = sprint_tokens_log_path()
    baseline_path = compact_baseline_path()

    current = count_closes(log_path)
    baseline = read_baseline(baseline_path)

    # First run with no baseline file: anchor to the current count so we count
    # closes from now forward rather than treating all history as pending.
    if not baseline_path.exists():
        write_baseline(baseline_path, current)
        return 0

    if not should_compact(current, baseline, COMPACT_EVERY_N):
        return 0

    # Update the baseline BEFORE injecting so the next Stop event (and the
    # compaction turn itself, which closes no ticket) sees delta 0 — no re-fire.
    write_baseline(baseline_path, current)

    if _tmux_session_exists(TMUX_SESSION):
        inject_autocompact(TMUX_SESSION)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A Stop hook must never block a turn — fail open.
        sys.exit(0)
