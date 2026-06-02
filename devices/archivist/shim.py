"""
ArchivistShim — lifecycle management for the Archivist device.

Manages start/stop/restart of the ArchivistDevice. On start(), registers the
device with the flat-file DeviceRegistry so the skeleton lists archivist with
agent_class=specialized.

No external process to manage (unlike InferenceShim's Ollama mode).
"""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim
from config.device_config import DeviceConfig
from skeleton.registry import DeviceRegistry
from devices.archivist.device import ArchivistDevice
from devices.inference.device import InferenceDevice

log = logging.getLogger(__name__)


class ArchivistShim(BaseShim):
    """Lifecycle shim for the Archivist device."""

    def __init__(
        self,
        inference: InferenceDevice | None = None,
        registry: DeviceRegistry | None = None,
    ) -> None:
        self._inference = inference
        self._registry = registry or DeviceRegistry()
        self._device: ArchivistDevice | None = None

    @property
    def device_id(self) -> str:
        return "archivist"

    @property
    def device(self) -> ArchivistDevice | None:
        return self._device

    def start(self) -> bool:
        self._device = ArchivistDevice(inference=self._inference)
        self._registry.register(
            device_id="archivist",
            config=DeviceConfig(),
            mailbox="comms://archivist/inbox",
            name="Archivist",
            agent_class="specialized",
        )
        log.info(
            "ArchivistShim: started, registered with skeleton (agent_class=specialized)"
        )
        return True

    def stop(self) -> bool:
        self._device = None
        log.info("ArchivistShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        if self._device is None:
            return {"passed": False, "details": "device not started"}
        try:
            depth = self._device.queue_depth()
            return {"passed": True, "details": f"proxy ready (queue_depth={depth})"}
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        self._device = None
        try:
            self._registry.deregister("archivist")
        except Exception:
            pass
        log.info("ArchivistShim: rollback complete")
