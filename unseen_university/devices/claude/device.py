"""
ClaudeDevice — rack registration for the Claude Code session.

Claude is a special device: it has no process that the rack manages.
The rack treats it as a persistent mailbox endpoint (CC.0 global +
CC.<session> per-session). Health = mailbox reachable. The YGM nudge
pipeline (ClaudeShim) injects 'ygm (N unread)' on each query submit.

Mailbox naming (locked D-adc-phase-0-2026-04-27):
  CC.0          — global broadcast, always present
  CC.<session>  — per-session, set via CLAUDE_SESSION_ID env var

comms() always returns the session mailbox (CC.N or CC.0 when no session).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.claude.constants import get_session_mailbox, GLOBAL_MAILBOX

_START_TIME = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaudeDevice(BaseDevice):
    """
    Device representing the active Claude Code session on this rack.

    Registers CC.0 (global) and the per-session mailbox so other devices
    can send messages without knowing Claude's session topology. The YGM
    nudge pipeline (managed by ClaudeShim) injects inbox summaries into
    Claude's context on each query submission.
    """

    DEVICE_ID = "claude"

    def __init__(self, imap_server=None) -> None:
        super().__init__()
        self._imap = imap_server
        self._blocked = False
        self._block_reason = ""

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        session_mailbox = get_session_mailbox()
        return {
            "device_id": self.DEVICE_ID,
            "name": "Claude Code",
            "version": "0.1.0",
            "purpose": "Claude Code session — receives nudges via YGM pipeline",
            "global_mailbox": GLOBAL_MAILBOX,
            "session_mailbox": session_mailbox,
        }

    def requirements(self) -> dict:
        return {
            "deps": [],
            "system": ["Claude Code CLI running", "message bus running"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": [],
            "mcp_endpoint": None,
            "supports_nudge": True,
        }

    def comms(self) -> dict:
        mailbox = get_session_mailbox()
        return {
            "address": f"comms://{mailbox}",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": True,
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
        if self._imap is None:
            return {
                "status": "degraded",
                "detail": "bus not configured — mailbox health unchecked",
                "checked_at": _now(),
            }
        mailboxes = self._imap.list_mailboxes()
        if GLOBAL_MAILBOX in mailboxes:
            return {
                "status": "healthy",
                "detail": f"Mailbox {GLOBAL_MAILBOX!r} present",
                "checked_at": _now(),
            }
        return {
            "status": "unhealthy",
            "detail": f"Mailbox {GLOBAL_MAILBOX!r} not found — ClaudeShim.start() needed",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        from unseen_university.config.device_config import unseen_university_logs

        return {"paths": {"chat": str(unseen_university_logs() / "CC.0")}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "localhost",
            "mailbox": get_session_mailbox(),
            "launch_command": "ClaudeShim().start()  # registers YGM hook",
        }

    def restart(self) -> None:
        self._blocked = False
        self._block_reason = ""

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason

    def halt(self) -> None:
        self._blocked = True
        self._block_reason = "halt requested"

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
