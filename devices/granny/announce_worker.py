"""
announce_worker.py — Worker self-announcement for Granny's dynamic dispatch registry.

Workers write a JSON file to ~/.granny/announced/<worker_id>.json at startup and
delete it at shutdown. Granny reads from this directory each poll cycle; pid-based
liveness checks reap entries from crashed workers.

Usage:
    from devices.granny.announce_worker import announce, withdraw

    announce("CC.1", mailbox="cc.1", worker_name="cc.1", one_at_a_time=True)
    ...
    withdraw("CC.1")

pid=0 in the record means "manually managed" — Granny never reaps these entries.
Useful for debugging or workers whose lifecycles aren't tied to a single process.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ANNOUNCE_DIR = Path.home() / ".granny" / "announced"


def announce(
    worker_id: str,
    *,
    mailbox: str,
    dispatch: str = "bus",
    worker_name: str | None = None,
    one_at_a_time: bool = False,
    cascade_if_idle: bool = False,
    pid: int | None = None,
) -> Path:
    """Write an announcement file for this worker. Returns the file path.

    worker_name — name stored in ticket metadata (e.g. "claude", "cc.1").
                  Defaults to worker_id lowercased with dots replaced by hyphens.
    pid         — process to liveness-check; defaults to os.getpid().
                  Pass 0 to opt out of reaping (manually managed).
    """
    _ANNOUNCE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "worker_id": worker_id,
        "mailbox": mailbox,
        "dispatch": dispatch,
        "worker_name": worker_name or worker_id.lower().replace(".", "-"),
        "one_at_a_time": one_at_a_time,
        "cascade_if_idle": cascade_if_idle,
        "pid": pid if pid is not None else os.getpid(),
        "announced_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _ANNOUNCE_DIR / f"{worker_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def withdraw(worker_id: str) -> None:
    """Delete the announcement file for this worker."""
    (_ANNOUNCE_DIR / f"{worker_id}.json").unlink(missing_ok=True)


def is_alive(pid: int) -> bool:
    """Return True if pid is still running (or pid==0 meaning manually managed)."""
    if pid == 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
