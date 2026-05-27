"""
IgorDevice — rack registration for the Igor agent process.

Health: tmux session check + optional IGOR_HOME instance log recency.
Credentials: none — Igor is a local process, no auth needed.
Restart semantics: SIGTERM to the running process; the exit-42 loop in the
launcher relaunches automatically. Forced restart (kill + relaunch) is
handled by IgorShim.

Configuration via environment variables:
  IGOR_TMUX_SESSION    — tmux session name Igor runs in (default: igor)
  IGOR_HOME            — Igor runtime dir (default: ~/.TheIgors/Igor-wild-0001)
  IGOR_LAUNCHER        — path to igor launcher script (default: igor)
# tags: Architecture, Infrastructure
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()
_DEFAULT_SESSION = os.environ.get("IGOR_TMUX_SESSION", "igor")
_DEFAULT_HOME = os.environ.get(
    "IGOR_HOME",
    os.path.expanduser("~/.unseen_university/Igor-wild-0001"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tmux_session_alive(session: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
            timeout=2,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class IgorDevice(BaseDevice):
    """
    Device representing the Igor agent process on this rack.

    Assumes Igor runs in a tmux session (default: 'igor'). If the session is
    absent, health() reports unhealthy. block() and halt() write flag files
    that Igor's watchdog reads on the next loop iteration.
    """

    DEVICE_ID = "igor"

    def __init__(
        self,
        tmux_session: str = _DEFAULT_SESSION,
        igor_home: str = _DEFAULT_HOME,
    ) -> None:
        super().__init__()
        self._session = tmux_session
        self._igor_home = igor_home
        self._blocked = False
        self._block_reason = ""

    # ── Primary operation ─────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return _tmux_session_alive(self._session)

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Igor",
            "version": "wild-0001",
            "purpose": "Graph-matrix reasoning agent with persistent Postgres memory",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["python3.12+", "psycopg2", "click", "fastmcp"],
            "system": ["tmux", "postgresql", "TheIgors repo at ~/TheIgors"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["igor_response", "habit_fired", "ne_cycle"],
            "mcp_endpoint": "http://127.0.0.1:8000",
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._blocked:
            return {
                "status": "unhealthy",
                "detail": f"blocked: {self._block_reason}",
                "checked_at": _now(),
            }
        alive = _tmux_session_alive(self._session)
        if alive:
            return {
                "status": "healthy",
                "detail": f"tmux session '{self._session}' is alive",
                "checked_at": _now(),
            }
        return {
            "status": "unhealthy",
            "detail": f"tmux session '{self._session}' not found",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {
            "paths": {
                "instance": os.path.join(self._igor_home, "instance_log.jsonl"),
            }
        }

    def update_info(self) -> dict:
        return {"current_version": "wild-0001", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "localhost",
            "tmux_session": self._session,
            "igor_home": self._igor_home,
            "launch_command": f"IgorShim().start()  # or: igor (bash alias)",
        }

    def restart(self) -> None:
        self._blocked = False
        self._block_reason = ""

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason
        flag = os.path.join(self._igor_home, "blocked.flag")
        try:
            with open(flag, "w") as f:
                f.write(reason)
        except OSError:
            pass

    def halt(self) -> None:
        self.block("halt requested")

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        flag = os.path.join(self._igor_home, "blocked.flag")
        try:
            os.unlink(flag)
        except FileNotFoundError:
            pass
