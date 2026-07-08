"""ScrapsShim — lifecycle shim for the Scraps job-runner (in-process, no subprocess).

ONE daemon structure (T-collapse-daemons-to-ground-loop): Scraps no longer runs a
standalone ``__main__`` + ``while True`` daemon spawned as a Ground-Loop ``PluginDaemon``
subprocess (the retired ``config/ground_loop/scraps.yaml``). Its maintenance jobs run in
an **in-process background thread this shim owns** (``ShimLoopThread``) — the aider
pattern. Unlike Granny, Scraps's jobs are unconditional maintenance, so the loop runs
continuously once ``start()`` is called (each job self-gates on its own interval).

Bring Scraps up with: ``python -m unseen_university.devices.scraps`` (see ``__main__.py``).
"""

from __future__ import annotations

import logging
from typing import Optional

from unseen_university.shim import BaseShim, ShimLoopThread

log = logging.getLogger(__name__)


class ScrapsShim(BaseShim):
    _device_id = "scraps"

    def __init__(self) -> None:
        self._loop: Optional[ShimLoopThread] = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        """Start the in-process job-runner loop (each job self-gates on its interval)."""
        if self._loop is not None and self._loop.is_alive():
            return True
        from unseen_university.devices.scraps.daemon import run_once, POLL_INTERVAL_S

        self._loop = ShimLoopThread(
            "scraps", run_once, POLL_INTERVAL_S, name="scraps-jobs"
        )
        self._loop.start()
        log.info("ScrapsShim: job-runner loop started (poll=%ds)", POLL_INTERVAL_S)
        return True

    def stop(self) -> bool:
        if self._loop is not None:
            self._loop.stop()
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """Health = job-runner loop liveness when started; else the validate_ticket
        capability check (so a not-yet-started shim still reports a meaningful shape)."""
        if self._loop is not None and self._loop.is_alive():
            return {"passed": True, "details": "job-runner loop thread running"}
        try:
            from unseen_university.devices.scraps.scraps_device import ScrapsDevice

            d = ScrapsDevice()
            result = d.validate_ticket(
                {
                    "title": "self-test ticket",
                    "description": "**Test plan:** call validate_ticket and check shape.",
                },
                silent=True,
            )
            if "valid" not in result or "issues" not in result:
                return {"passed": False, "details": f"unexpected shape: {result}"}
            return {
                "passed": True,
                "details": "loop not started; validate_ticket returned expected shape",
            }
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        if self._loop is not None:
            self._loop.stop()

    def _handle_non_skill(self, text: str) -> str:
        import hashlib
        _BARKS = ["Woof!", "Grr!", "Bark!", "Yip!", "Ruff!"]
        idx = int(hashlib.md5(text.encode()).hexdigest(), 16) % len(_BARKS)
        return _BARKS[idx]
