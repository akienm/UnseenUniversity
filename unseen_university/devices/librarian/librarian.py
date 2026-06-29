"""The Librarian — always-on rack device.

The Librarian is the rack-minion, renamed. Runs whenever the rack is running,
independent of Igor. Handles: MCP surface for CC, IMAP/bus inter-agent comms,
web chat interface, DB proxy, inference routing (phase 2+).

Discworld canon: the Librarian's word is OOK.
"""

from __future__ import annotations

import os

from unseen_university._uu_root import uu_home
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

OOK = "Ook."  # the Librarian's only word

_VERSION = "0.1.0"
_LOG_ROOT = Path(uu_home()) / "logs"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class Librarian(BaseDevice):
    """BaseDevice subclass — skeleton phase.

    Phase 1 (this ticket): rack contract satisfied, MCP server stub, health check.
    Phase 2: tool inventory, inference routing, research capability.
    Phase 3: DB proxy, health aggregation via heartbeat IDLE.
    """

    def __init__(self) -> None:
        super().__init__(device_id="librarian")
        self._started_at = time.monotonic()
        self._startup_errors: list[str] = []
        self._blocked: str | None = None
        self._halted = False

    # ── Identity ─────────────────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": "librarian",
            "name": "The Librarian",
            "version": _VERSION,
            "ook": OOK,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    # ── Requirements / capabilities ───────────────────────────────────────────

    def requirements(self) -> dict:
        return {
            "deps": [
                "unseen_university",
            ]
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": [],  # expanded when tools land
            "mcp_server": True,
            "inference_routing": False,  # phase 2
            "db_proxy": False,  # phase 3
            "research": False,  # phase 3
        }

    def comms(self) -> dict:
        return {
            "address": "comms://librarian",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": True,
        }

    # ── Health + lifecycle ────────────────────────────────────────────────────

    def health(self) -> dict:
        if self._halted:
            status, detail = "unhealthy", "halted"
        elif self._blocked:
            status, detail = "degraded", f"blocked: {self._blocked}"
        else:
            status, detail = "healthy", OOK
        return {
            "status": status,
            "detail": detail,
            "checked_at": _ts(),
        }

    def uptime(self) -> float:
        return time.monotonic() - self._started_at

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        base = _LOG_ROOT / "librarian"
        return {
            "paths": {
                "main": str(base / "librarian.log"),
                "perf": str(base / "perf"),
            }
        }

    def update_info(self) -> dict:
        return {
            "current_version": _VERSION,
            "update_available": False,
        }

    def where_and_how(self) -> dict:
        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "launch_command": "python -m unseen_university.devices.librarian",
        }

    # ── Lifecycle control ─────────────────────────────────────────────────────

    def restart(self) -> None:
        self._blocked = None
        self._halted = False
        self._started_at = time.monotonic()
        self._startup_errors = []

    def block(self, reason: str) -> None:
        self._blocked = reason

    def halt(self) -> None:
        self._halted = True

    def recovery(self) -> None:
        self._blocked = None
        self._halted = False
