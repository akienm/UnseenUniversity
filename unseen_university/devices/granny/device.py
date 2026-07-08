"""
GrannyWeatherwaxDevice — ticket gateway and coding orchestrator.

Granny receives tickets from cc_queue, routes them to the appropriate
worker (CC.0, DickSimnel, minion) based on role/difficulty, dispatches
via tmux send-keys or inference, and monitors for stalls.

Named for Granny Weatherwax from Discworld — she knows who to send.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GrannyWeatherwaxDevice(BaseDevice):
    """Ticket gateway: routes sprint tickets to the right worker."""

    DEVICE_ID = "granny"

    def __init__(self) -> None:
        super().__init__(device_id=self.DEVICE_ID)
        self._errors: list[str] = []
        # Wire the shim (aider pattern) — the shim owns Granny's demand-started,
        # in-process dispatch loop. Nothing wired the shim before the daemon
        # collapse, so the shim-owns-startup model was never actually live
        # (T-collapse-daemons-to-ground-loop).
        from unseen_university.devices.granny.shim import GrannyShim

        self._shim = GrannyShim()

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Granny Weatherwax",
            "version": "0.1.0",
            "purpose": "Route sprint tickets to CC.0, DickSimnel, or minion workers",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["cc_queue.py", "tmux"],
            "system": ["tmux session for CC dispatch"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["GRANNY_DISPATCH", "GRANNY_STALL"],
            "mcp_endpoint": None,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
        }

    def health(self) -> dict:
        if self._errors:
            return {
                "status": "degraded",
                "detail": self._errors[-1],
                "checked_at": _now_iso(),
            }
        return {"status": "healthy", "checked_at": _now_iso()}

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m unseen_university.devices.granny",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()
