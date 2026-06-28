"""
SwadlDevice — rack registration stub for the SWADL testing framework.

SWADL (~/dev/src/swadl) is vendored but its API is not stable enough for
full device integration. This stub reserves the rack slot and documents the
planned interface. Replace with real implementation once API stabilizes.

Planned capabilities (post-stabilization):
- run_flow(flow_spec: dict) → FlowResult
- describe_capabilities() → list[FlowCapability]
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()
_STUB_DETAIL = "SWADL API not stable — stub pending API stabilization"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SwadlDevice(BaseDevice):
    """
    Stub device for SWADL. Returns documented stubs for all contract methods.
    Replace with real implementation once SWADL API stabilizes.
    """

    DEVICE_ID = "swadl"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "SWADL",
            "version": "stub",
            "purpose": "Structured Workflow Agent Definition Language — test flow runner (stub)",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["swadl>=0.2.0"],
            "system": ["swadl installed via pip install -e ~/dev/src/swadl"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "mcp_endpoint": None,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_only",
            "supports_push": False,
            "supports_pull": False,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        return {
            "status": "degraded",
            "detail": _STUB_DETAIL,
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "stub", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "localhost",
            "pid": -1,
            "launch_command": "not yet implemented — stub",
        }

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        pass

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        pass
