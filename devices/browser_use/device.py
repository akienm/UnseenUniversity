"""
BrowserUseDevice — browser automation device for unseen_university.

Wraps browser-use Agent dispatch behind the BaseDevice contract. The shim
(BrowserUseShim) manages the Chrome process lifetime; this device manages
task dispatch against the running browser.

Per T-adc-browser-use-eval-spike findings:
- Interface: browser_use.agent.service.Agent + browser_use.browser.session.BrowserSession
- Pin: browser-use>=0.12,<0.13
- Import from submodule paths (not top-level — lazy-loaded and unstable)
- restart() = stop() + start() on the shim (kill-and-reopen)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()
_CDP_PORT = int(os.environ.get("BROWSER_USE_CDP_PORT", "9222"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrowserUseDevice(BaseDevice):
    """
    Device that dispatches LLM-driven browser automation tasks.

    Requires a running Chrome on localhost:{cdp_port} (managed by BrowserUseShim).
    Primary operation: run_task(task, llm, max_steps) → str result.
    """

    DEVICE_ID = "browser-use"

    def __init__(self, cdp_port: int = _CDP_PORT) -> None:
        super().__init__()
        self._cdp_port = cdp_port
        self._blocked = False
        self._block_reason = ""

    # ── Primary operation ─────────────────────────────────────────────────────

    async def run_task(self, task: str, llm, max_steps: int = 20) -> str:
        """
        Run a browser-use Agent task against the connected Chrome session.

        Returns the agent's final result string.
        Raises RuntimeError if the device is blocked or Chrome is unreachable.
        """
        if self._blocked:
            raise RuntimeError(f"BrowserUseDevice is blocked: {self._block_reason}")
        from browser_use.agent.service import Agent
        from browser_use.browser.session import BrowserSession

        session = BrowserSession(cdp_url=f"http://127.0.0.1:{self._cdp_port}")
        agent = Agent(task=task, llm=llm, browser_session=session)
        history = await agent.run(max_steps=max_steps)
        return history.final_result() or ""

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "BrowserUse",
            "version": "0.1.0",
            "purpose": "LLM-driven browser automation via browser-use Agent",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["browser-use>=0.12,<0.13", "playwright"],
            "system": ["google-chrome or chromium-browser in PATH"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["browser_task_result"],
            "mcp_endpoint": None,
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
        if self._blocked:
            return {
                "status": "unhealthy",
                "detail": f"blocked: {self._block_reason}",
                "checked_at": _now(),
            }
        # Quick port reachability check
        import socket

        try:
            with socket.create_connection(("127.0.0.1", self._cdp_port), timeout=1):
                pass
            return {
                "status": "healthy",
                "detail": f"Chrome CDP responding on port {self._cdp_port}",
                "checked_at": _now(),
            }
        except OSError:
            return {
                "status": "unhealthy",
                "detail": f"Chrome CDP not responding on port {self._cdp_port}",
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
        return {
            "host": "localhost",
            "pid": os.getpid(),
            "launch_command": f"BrowserUseShim().start()  # Chrome on port {self._cdp_port}",
        }

    def restart(self) -> None:
        # Restart is handled by BrowserUseShim (kill Chrome + relaunch).
        # The device itself is stateless between tasks.
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
