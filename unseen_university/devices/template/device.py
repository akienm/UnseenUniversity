"""
TemplateDevice — hello-world starter for unseen_university.

Copy this folder. Rename TemplateDevice to YourAgentDevice.
Fill in the stubs. Register with the rack.

This device intentionally implements every BaseDevice method with a sensible
stub so it passes isinstance checks and the contract test suite immediately.
"""

from __future__ import annotations

import time
from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()


class TemplateDevice(BaseDevice):
    # Replace this with your actual device name
    DEVICE_ID = "template"

    def who_am_i(self) -> dict:
        # Replace with your agent's identity information
        return {
            "device_id": self.DEVICE_ID,
            "name": "Template Agent",
            "version": "0.1.0",
            "purpose": "Replace this with a description of what your agent does",
        }

    def requirements(self) -> dict:
        # Replace with your actual runtime dependencies
        return {
            "deps": [],  # e.g. ["psycopg2", "requests"]
        }

    def capabilities(self) -> dict:
        # Replace with what your agent actually emits/consumes
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": [],  # e.g. ["result", "error"]
        }

    def comms(self) -> dict:
        # Replace with your actual comms:// address
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
        # Replace with an actual health check — ping a port, check a file, etc.
        return {
            "status": "healthy",
            "detail": "stub — replace with real health check",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        # Replace with errors captured during __init__ / start
        return []

    def logs(self) -> dict:
        # Replace with actual log paths once LoggingControlCenter is wired
        return {"paths": {}}

    def update_info(self) -> dict:
        # Replace with version check logic
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        import os

        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": f"python -m unseen_university.devices.{self.DEVICE_ID}.device",
        }

    def restart(self) -> None:
        # Replace with your restart logic (delegate to shim)
        pass

    def block(self, reason: str) -> None:
        # Replace with block handling — prevent the rack from restarting this device
        pass

    def halt(self) -> None:
        # Replace with graceful shutdown logic
        pass

    def recovery(self) -> None:
        # Replace with recovery logic for degraded/unhealthy state
        pass


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
