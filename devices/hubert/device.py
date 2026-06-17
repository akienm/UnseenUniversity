"""
HubertDevice — Development Process Controller and Infrastructure Owner.

Hubert owns: lab/, tickets, decisions, outcomes, palace browser, goals,
racks, and rack infrastructure. His fascia page in the web UI exposes all
dev-process tooling (Goals, Decisions, Hypotheses, Outcomes, Palace Browser).
"""

from __future__ import annotations

import logging
import time

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()


class HubertDevice(BaseDevice):
    DEVICE_ID = "hubert"

    def __init__(self) -> None:
        log.info("BOOT_START device=hubert")

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Hubert",
            "version": "0.1.0",
            "purpose": (
                "Development process controller and infrastructure owner. "
                "Owns lab/, tickets, decisions, outcomes, palace browser, "
                "goals, racks, and rack infrastructure."
            ),
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "mcp_tools": ["constraints_get", "constraints_ingest"],
            "owns": ["lab/", "tickets", "decisions", "outcomes", "palace", "goals", "racks"],
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
            "launch_command": "python -m devices.hubert.device",
        }

    def restart(self) -> None:
        log.info("RESTART device=hubert")

    def block(self, reason: str) -> None:
        log.warning("BLOCK device=hubert reason=%r", reason)

    def halt(self) -> None:
        log.info("HALT device=hubert")

    def recovery(self) -> None:
        log.info("RECOVERY device=hubert")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    device = HubertDevice()
    log.info("BOOT_READY device=hubert version=%s", device.who_am_i()["version"])

    # Register in flat-file registry so web UI /api/device/list picks up Hubert
    try:
        from skeleton.registry import DeviceRegistry
        from config.device_config import DeviceConfig
        registry = DeviceRegistry()
        registry.register(
            device_id="hubert",
            config=DeviceConfig(),
            mailbox="comms://hubert/inbox",
            name="Hubert",
            agent_class="utility",
        )
        log.info("REGISTERED device=hubert in flat-file registry")
    except Exception as exc:
        log.warning("REGISTRY_FAIL device=hubert error=%s", exc)

    health = device.health()
    log.info("HEALTH device=hubert status=%s uptime_s=%s", health["status"], health["uptime_s"])


if __name__ == "__main__":
    main()
