"""
ArchivistDevice — compiled-inference proxy layer and overnight learning pipeline.

Wraps InferenceDevice: every inference call routes through the proxy before
reaching the LLM. Proxy pre-checks the knowledge graph (stub: always-miss);
on miss, dispatches to InferenceDevice and fans out a learning payload to
the overnight pipeline.

Caller API: identical to InferenceDevice.dispatch() — same request/response types.
The Archivist is a drop-in wrapper; no call-site changes required.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse
from unseen_university.devices.archivist.proxy import ArchivistProxy

log = logging.getLogger(__name__)

_START_TIME = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchivistDevice(BaseDevice):
    """
    Rack device: compiled-inference proxy + overnight learning pipeline.

    Primary entry point: dispatch(InferenceRequest) -> InferenceResponse.
    Wraps an InferenceDevice — all inference traffic that flows through this
    device is proxy-intercepted before reaching the LLM.
    """

    DEVICE_ID = "archivist"
    AGENT_CLASS = "specialized"

    def __init__(self, inference: InferenceDevice | None = None) -> None:
        super().__init__()
        self._inference = inference or InferenceDevice()
        self._proxy = ArchivistProxy()

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Archivist",
            "version": "0.1.0",
            "purpose": "Compiled-inference proxy + overnight knowledge-graph pipeline",
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {
            "deps": ["InferenceDevice"],
            "system": [],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["proxy_intercept", "learning_payload"],
            "mcp_endpoint": None,
            "public_methods": ["dispatch", "queue_depth"],
            "agent_class": self.AGENT_CLASS,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        inf_h = self._inference.health()
        if inf_h.get("status") != "healthy":
            return {
                "status": "degraded",
                "detail": f"InferenceDevice: {inf_h.get('detail')}",
                "checked_at": _now(),
            }
        return {
            "status": "healthy",
            "detail": f"proxy ready, learning queue depth={self._proxy.pipeline.queue_depth()}",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return self._inference.startup_errors()

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "localhost",
            "pid": os.getpid(),
            "launch_command": "ArchivistShim().start()",
        }

    def restart(self) -> None:
        self._proxy = ArchivistProxy()
        log.info("ArchivistDevice: restarted (new proxy)")

    def block(self, reason: str) -> None:
        log.warning("ArchivistDevice blocked: %s", reason)

    def halt(self) -> None:
        log.warning("ArchivistDevice halted")

    def recovery(self) -> None:
        log.info("ArchivistDevice: recovery")

    # ── Inference dispatch (proxy layer) ──────────────────────────────────────

    def dispatch(self, request: InferenceRequest) -> InferenceResponse:
        """Route inference through the proxy. API identical to InferenceDevice.dispatch()."""
        return self._proxy.intercept(request, self._inference.dispatch)

    def queue_depth(self) -> int:
        """Return the current depth of the overnight learning queue."""
        return self._proxy.pipeline.queue_depth()
