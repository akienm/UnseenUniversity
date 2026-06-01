"""
stop_cc_minions.py — Kill all cc-T-* tmux sessions and orphaned claude sprint processes.

Does NOT touch: claude-main, granny, web-server, igor.

Usage:
  python3 stop_cc_minions.py          # dry-run summary then kill
  python3 stop_cc_minions.py --dry-run
  python3 stop_cc_minions.py --quiet  # no stdout, channel post only

Posts CC_MINIONS_STOPPED to the granny-weatherwax channel on success.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(_UU_ROOT))

_PROTECTED_SESSIONS = frozenset({"claude-main", "granny", "web-server", "igor"})


def _list_cc_sessions() -> list[str]:
    """Return tmux session names that start with 'cc-'."""
    try:
        result = subprocess.run(
            ["tmux", "ls", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return [l for l in result.stdout.splitlines() if l.startswith("cc-")]
    except Exception:
        return []


def _list_sprint_pids() -> list[int]:
    """Return PIDs of 'claude.*sprint-ticket' processes."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = []
        for line in result.stdout.splitlines():
            if "claude" in line and "sprint-ticket" in line and "grep" not in line:
                try:
                    pids.append(int(line.split()[1]))
                except (IndexError, ValueError):
                    pass
        return pids
    except Exception:
        return []


def _kill_sessions(sessions: list[str], dry_run: bool, quiet: bool) -> int:
    killed = 0
    for name in sessions:
        if name in _PROTECTED_SESSIONS:
            if not quiet:
                print(f"  SKIP (protected): {name}")
            continue
        if dry_run:
            if not quiet:
                print(f"  DRY-RUN kill-session: {name}")
        else:
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", name], timeout=5, check=False
                )
                if not quiet:
                    print(f"  killed session: {name}")
                killed += 1
            except Exception as e:
                if not quiet:
                    print(f"  ERROR killing {name}: {e}")
    return killed


def _kill_pids(pids: list[int], dry_run: bool, quiet: bool) -> int:
    killed = 0
    for pid in pids:
        if dry_run:
            if not quiet:
                print(f"  DRY-RUN SIGTERM pid={pid}")
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                if not quiet:
                    print(f"  SIGTERM pid={pid}")
                killed += 1
            except ProcessLookupError:
                pass
            except Exception as e:
                if not quiet:
                    print(f"  ERROR sending SIGTERM to {pid}: {e}")
    return killed


def _verify_clear(max_wait: float = 5.0) -> bool:
    """Return True when all cc-* sessions and sprint PIDs are gone within max_wait seconds."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        sessions = _list_cc_sessions()
        pids = _list_sprint_pids()
        if not sessions and not pids:
            return True
        time.sleep(0.5)
    return False


def _post_channel(msg: str) -> None:
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(msg, author="granny-weatherwax", channel="granny-weatherwax")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill all CC minion sessions")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    sessions = _list_cc_sessions()
    pids = _list_sprint_pids()

    if not sessions and not pids:
        if not args.quiet:
            print("stop_cc_minions: nothing to kill — all clear")
        return 0

    if not args.quiet:
        print(
            f"stop_cc_minions: found {len(sessions)} cc-* session(s), {len(pids)} sprint PID(s)"
        )
        for s in sessions:
            print(f"  session: {s}")
        for p in pids:
            print(f"  pid: {p}")

    s_killed = _kill_sessions(sessions, args.dry_run, args.quiet)
    p_killed = _kill_pids(pids, args.dry_run, args.quiet)

    if args.dry_run:
        if not args.quiet:
            print(
                f"stop_cc_minions: DRY-RUN complete (would kill {s_killed} sessions, {p_killed} pids)"
            )
        return 0

    # Brief pause then SIGKILL stragglers
    time.sleep(1.5)
    remaining_pids = _list_sprint_pids()
    for pid in remaining_pids:
        try:
            os.kill(pid, signal.SIGKILL)
            if not args.quiet:
                print(f"  SIGKILL pid={pid} (straggler)")
        except Exception:
            pass

    clear = _verify_clear()
    summary = (
        f"CC_MINIONS_STOPPED|sessions={s_killed}|pids={p_killed}"
        f"|verified={'ok' if clear else 'timeout'}"
    )
    _post_channel(summary)
    if not args.quiet:
        print(f"stop_cc_minions: {summary}")

    return 0 if clear else 1


if __name__ == "__main__":
    sys.exit(main())
