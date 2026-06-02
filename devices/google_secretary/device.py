"""
GoogleSecretaryDevice — Google Workspace automation device for the rack bus.

Receives structured requests via the bus (from Igor, Granny, etc.), routes them
through the graph-tree dispatcher to the correct Google operation, and replies
with results. Escalates ambiguous requests to the human channel.

Auth: OAuth 2.0 with token storage in Postgres or flat file (no SQLite).
Supports: Calendar CRUD, Gmail send/read/forward/search, Tasks CRUD.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.shim import BaseShim

from .dispatcher import GoogleSecretaryDispatcher
from .shim import GoogleSecretaryShim

log = logging.getLogger(__name__)

_START_TIME = time.time()
_DEFAULT_HOME = Path.home() / ".unseen_university" / "google_secretary"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GoogleSecretaryDevice(BaseDevice):
    """
    Rack device for Google Workspace operations.

    The device owns the dispatcher and shim. The rack calls lifecycle methods;
    inbound requests arrive via the bus, get routed by the dispatcher, and
    results are sent back via the shim's reply channel.
    """

    DEVICE_ID = "google_secretary"

    def __init__(
        self,
        home: str | Path = _DEFAULT_HOME,
        token_storage: str = "file",
    ) -> None:
        super().__init__()
        self._home = Path(home)
        self._home.mkdir(parents=True, exist_ok=True)
        self._token_storage = token_storage
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []

        # Sub-components (created lazily)
        self._dispatcher: GoogleSecretaryDispatcher | None = None
        self._shim: GoogleSecretaryShim | None = None

    # ── Property accessors ──────────────────────────────────────────────────

    @property
    def dispatcher(self) -> GoogleSecretaryDispatcher:
        if self._dispatcher is None:
            self._dispatcher = GoogleSecretaryDispatcher(home=self._home)
        return self._dispatcher

    @property
    def shim(self) -> GoogleSecretaryShim:
        if self._shim is None:
            self._shim = GoogleSecretaryShim(
                home=self._home, token_storage=self._token_storage
            )
        return self._shim

    # ── BaseDevice contract ─────────────────────────────────────────────────

    AGENT_CLASS = "utility"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Google Secretary",
            "version": "0.1.0",
            "purpose": "Google Workspace automation — calendar, gmail, tasks via rack bus",
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {
            "deps": [
                "python3.12+",
                "google-api-python-client",
                "google-auth-oauthlib",
                "google-auth-httplib2",
            ],
            "system": [
                "OAuth 2.0 credentials at ~/.unseen_university/google_secretary/credentials.json",
            ],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": [
                "google_calendar_event",
                "google_email",
                "google_task",
                "google_error",
                "escalate_to_channel",
            ],
            "mcp_tools": [
                "calendar_create",
                "calendar_read",
                "calendar_delete",
                "gmail_send",
                "gmail_read",
                "gmail_forward",
                "gmail_search",
                "tasks_create",
                "tasks_read",
                "tasks_delete",
            ],
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
        try:
            test = self.shim.self_test()
            if test.get("passed"):
                return {
                    "status": "healthy",
                    "detail": "shim self-test passed, dispatcher ready",
                    "checked_at": _now(),
                }
            return {
                "status": "degraded",
                "detail": f"shim self-test: {test.get('details', 'unknown')}",
                "checked_at": _now(),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "detail": f"health check failed: {exc}",
                "checked_at": _now(),
            }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return self._startup_errors

    def logs(self) -> dict:
        return {
            "paths": {
                "dispatcher": str(self._home / "dispatcher.log"),
                "shim_trace": str(self._home / "shim_trace.log"),
                "oauth": str(self._home / "oauth.log"),
            }
        }

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "home": str(self._home),
            "launch_command": "python -m devices.google_secretary.device",
        }

    def restart(self) -> None:
        """Restart the device — clear block, reinitialize dispatcher and shim."""
        self._blocked = False
        self._block_reason = ""
        self._dispatcher = None
        self._shim = None
        log.info("GoogleSecretaryDevice: restarted")

    def block(self, reason: str) -> None:
        """Block this device from operating. Rack will not auto-relaunch."""
        self._blocked = True
        self._block_reason = reason
        log.warning("GoogleSecretaryDevice: blocked — %s", reason)
        flag = self._home / "blocked.flag"
        flag.write_text(reason)

    def halt(self) -> None:
        """Halt immediately."""
        self.block("halt requested")

    def recovery(self) -> None:
        """Attempt recovery from degraded state."""
        self._blocked = False
        self._block_reason = ""
        self._startup_errors = []
        self._dispatcher = None
        self._shim = None
        flag = self._home / "blocked.flag"
        try:
            flag.unlink()
        except FileNotFoundError:
            pass
        log.info("GoogleSecretaryDevice: recovery attempted")

    # ── Inbound request handling ────────────────────────────────────────────

    def handle_request(self, request: dict) -> dict:
        """
        Accept a structured request from the bus and route it through the dispatcher.

        Request shape:
            {
                "action": str,           # e.g. "calendar_create", "gmail_send"
                "params": dict,          # tool-specific parameters
                "request_id": str,       # correlation ID for reply
                "from_device": str,      # origin device ID
            }

        Returns the dispatcher result dict:
            {
                "status": "ok" | "error" | "escalate",
                "result": ...,
                "error": str | None,
                "request_id": str,
            }
        """
        action = request.get("action", "")
        params = request.get("params", {})
        request_id = request.get("request_id", "")

        log.info(
            "handle_request: action=%s request_id=%s from=%s",
            action,
            request_id,
            request.get("from_device", "unknown"),
        )

        result = self.dispatcher.dispatch(action=action, params=params)
        return {
            "status": result.get("status", "error"),
            "result": result.get("result"),
            "error": result.get("error"),
            "request_id": request_id,
        }
