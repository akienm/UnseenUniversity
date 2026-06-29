"""sqlite_caller_trace.py — Debug patch: log sqlite3.connect() callstacks.

Activated by env var IGOR_TRACE_SQLITE_CALLERS=1.
Writes to ~/.unseen_university/local/logs/sqlite_caller_trace.log.

Usage:
    Set IGOR_TRACE_SQLITE_CALLERS=1 in ~/.unseen_university/Igor-Wild1/.env, then
    restart Igor and run a user turn that previously triggered:
      sqlite3.OperationalError: no such table: config
    Read the log to find which module+function called sqlite3.connect() and
    which DB path it opened. File the migration ticket for that caller.

CLEANUP: Remove the wiring from main.py and delete this file once the
offending caller is identified (unseenuniversity/rules/safeguards — debug
instrumentation must not stay in prod). T-sqlite-config-hunt.
"""

from __future__ import annotations

import os
import sqlite3
import traceback
import threading
from pathlib import Path

_LOG_PATH = Path(os.path.expanduser("~/.unseen_university/local/logs/sqlite_caller_trace.log"))
_lock = threading.Lock()
_original_connect = sqlite3.connect
_installed = False


def _patched_connect(database, *args, **kwargs):
    stack = "".join(traceback.format_stack())
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(_LOG_PATH, "a") as f:
            f.write(f"\n--- sqlite3.connect({database!r}) ---\n")
            f.write(stack)
    return _original_connect(database, *args, **kwargs)


def install() -> bool:
    """Monkey-patch sqlite3.connect to log all callers. Returns True if installed."""
    global _installed
    if os.getenv("IGOR_TRACE_SQLITE_CALLERS") != "1":
        return False
    if _installed:
        return True
    sqlite3.connect = _patched_connect
    _installed = True
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "w") as f:
        f.write("sqlite3.connect tracer active (T-sqlite-config-hunt)\n")
        f.write(f"Set IGOR_TRACE_SQLITE_CALLERS=0 and restart to deactivate.\n\n")
    return True
