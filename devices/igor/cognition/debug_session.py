"""
debug_session.py — Debug session state manager (DESIGNED:T-mcp-igor-cognition-debug-capability)

Replaces the raw flag-file dance with a first-class API:
  claim(scope)   → handle (str)
  status(handle) → dict
  release(handle)
  query(handle)  → list[str]

Backwards-compat: still writes/removes debug_session.flag so Igor's existing
main.py crash-recovery check (main.py:3350) keeps working during MCP burn-in.

MCP surface (via unseen_university when it arrives) will call these functions.
Until then, devlab/claudecode/debug_session_cli.py wraps them for skill invocation.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from devices.igor.paths import paths as _paths

log = logging.getLogger(__name__)


def _flag_path() -> Path:
    return _paths().instance / "debug_session.flag"


def _state_path() -> Path:
    return _paths().instance / "debug_session_state.json"


def _read_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as e:
            log.debug("_read_state: json.loads failed: %s", e)
    return {}


def _write_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2))


def claim(scope: str = "session") -> str:
    """Claim a debug session. Returns a handle string."""
    handle = f"dbg-{uuid.uuid4().hex[:8]}"
    state = {
        "handle": handle,
        "scope": scope,
        "claimed_at": time.time(),
        "log": [],
    }
    _write_state(state)
    # Backwards-compat: write the flag so main.py:3350 still works
    _flag_path().touch()
    return handle


def status(handle: Optional[str] = None) -> dict:
    """Return current debug session status."""
    state = _read_state()
    if not state:
        return {"active": False}
    if handle and state.get("handle") != handle:
        return {"active": False, "error": "handle mismatch"}
    return {
        "active": True,
        "handle": state["handle"],
        "scope": state.get("scope", "session"),
        "claimed_at": state.get("claimed_at"),
        "age_s": time.time() - state.get("claimed_at", time.time()),
        "log_lines": len(state.get("log", [])),
    }


def release(handle: Optional[str] = None) -> bool:
    """Release the debug session. Returns True if released, False if no session."""
    state = _read_state()
    if not state:
        return False
    if handle and state.get("handle") != handle:
        return False
    _state_path().unlink(missing_ok=True)
    _flag_path().unlink(missing_ok=True)
    return True


def log_line(handle: str, line: str) -> None:
    """Append a log line to the debug session."""
    state = _read_state()
    if not state or state.get("handle") != handle:
        return
    state.setdefault("log", []).append({"ts": time.time(), "line": line})
    _write_state(state)


def query(handle: Optional[str] = None, limit: int = 50) -> list[str]:
    """Return recent debug log lines."""
    state = _read_state()
    if not state:
        return []
    if handle and state.get("handle") != handle:
        return []
    entries = state.get("log", [])[-limit:]
    return [e["line"] for e in entries]
