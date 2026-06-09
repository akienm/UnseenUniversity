"""
PonderDevice — Human-Facing Coordinator.

Scaffold device. Coordinator functionality (natural-language system-state
queries, human<->device routing) is a future sprint.

D-ponder-stibbons-device-2026-06-09
"""
from __future__ import annotations

import logging
import time

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()


class PonderDevice(BaseDevice):
    DEVICE_ID = "ponder"

    def __init__(self) -> None:
        log.info("BOOT_START device=ponder")

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Ponder Stibbons",
            "version": "0.1.0",
            "purpose": (
                "Human-facing coordinator. Bridges human<->system without "
                "the user needing to know which device owns what. "
                "Plain-language system summaries and query routing."
            ),
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "owns": ["human-facing queries", "system-state summaries"],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": False,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        return {
            "status": "healthy",
            "uptime_s": round(time.time() - _START_TIME, 1),
            "checked_at": _now(),
            "note": "scaffold — coordinator functionality not yet implemented",
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        import os
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "python -m devices.ponder.device",
        }

    def restart(self) -> None:
        log.info("RESTART device=ponder")

    def block(self, reason: str) -> None:
        log.warning("BLOCK device=ponder reason=%r", reason)

    def halt(self) -> None:
        log.info("HALT device=ponder")

    def recovery(self) -> None:
        log.info("RECOVERY device=ponder")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    device = PonderDevice()
    log.info("BOOT_READY device=ponder version=%s", device.who_am_i()["version"])

    try:
        from skeleton.registry import DeviceRegistry
        from config.device_config import DeviceConfig
        DeviceRegistry().register(
            device_id="ponder",
            config=DeviceConfig(),
            mailbox="comms://ponder/inbox",
            name="Ponder Stibbons",
            agent_class="utility",
        )
        log.info("REGISTERED device=ponder in flat-file registry")
    except Exception as exc:
        log.warning("REGISTRY_FAIL device=ponder error=%s", exc)

    health = device.health()
    log.info("HEALTH device=ponder status=%s", health["status"])


if __name__ == "__main__":
    main()
