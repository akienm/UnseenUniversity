"""
MinionDevice — executes sprint tickets via cheap inference + tool loop.

Receives a WorkerEnvelope (ticket id + description + repo-map context),
runs ToolLoop until DONE or ESCALATE, returns WorkerResult.

Dependency injection: pass an InferenceDevice to share one instance across
devices. If omitted, MinionDevice creates its own.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.minion.shim import WorkerEnvelope, WorkerResult
from unseen_university.devices.minion.tool_loop import ToolLoop

log = logging.getLogger(__name__)

_START_TIME = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MinionDevice(BaseDevice):
    """
    Rack device that executes sprint tickets using cheap inference.

    Primary entry point: execute(WorkerEnvelope) -> WorkerResult.
    """

    DEVICE_ID = "minion"

    def __init__(self, inference: InferenceDevice | None = None) -> None:
        super().__init__()
        self._inference = inference or InferenceDevice()
        self._loop = ToolLoop(self._inference)
        self._runs: list[dict] = []

    # ── BaseDevice contract ───────────────────────────────────────────────────

    AGENT_CLASS = "utility"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Minion Worker",
            "version": "0.1.0",
            "purpose": "Execute sprint tickets via cheap inference + MCP tool loop",
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {
            "deps": ["InferenceDevice"],
            "system": ["OPENROUTER_API_KEY or local Ollama"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["worker_result"],
            "mcp_endpoint": None,
            "public_methods": ["execute", "run_history"],
        }

    def comms(self) -> dict:
        return {
            "address": "comms://minion/worker-inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        inf_health = self._inference.health()
        if inf_health.get("status") != "healthy":
            return {
                "status": "degraded",
                "detail": f"InferenceDevice unhealthy: {inf_health.get('detail')}",
                "checked_at": _now(),
            }
        return {"status": "healthy", "detail": "ready", "checked_at": _now()}

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return self._inference.startup_errors()

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        import os

        return {
            "host": "localhost",
            "pid": os.getpid(),
            "launch_command": "MinionDevice().execute(WorkerEnvelope(...))",
        }

    def restart(self) -> None:
        self._loop = ToolLoop(self._inference)
        log.info("MinionDevice: restarted (new ToolLoop)")

    def block(self, reason: str) -> None:
        log.warning("MinionDevice blocked: %s", reason)

    def halt(self) -> None:
        log.warning("MinionDevice halted")

    def recovery(self) -> None:
        log.info("MinionDevice: recovery")

    # ── Work execution ────────────────────────────────────────────────────────

    def execute(self, envelope: WorkerEnvelope) -> WorkerResult:
        """Run the tool loop for one ticket. Returns WorkerResult."""
        log.info(
            "MinionDevice.execute: ticket=%s session=%s",
            envelope.ticket_id,
            envelope.session_id,
        )
        t0 = time.time()
        result = self._loop.run(envelope)
        elapsed = round(time.time() - t0, 1)

        self._runs.append(
            {
                "ticket_id": envelope.ticket_id,
                "signal": result.signal,
                "iterations": result.iterations,
                "tools": result.tools_called,
                "elapsed_s": elapsed,
                "cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "at": _now(),
            }
        )
        log.info(
            "MinionDevice.execute: %s → signal=%r iterations=%d elapsed=%.1fs "
            "cost_usd=%.4f in_tok=%d out_tok=%d",
            envelope.ticket_id,
            result.signal,
            result.iterations,
            elapsed,
            result.cost_usd,
            result.input_tokens,
            result.output_tokens,
        )
        return result

    def run_history(self) -> list[dict]:
        """Return log of recent executions (in-memory, resets on restart)."""
        return list(self._runs)
