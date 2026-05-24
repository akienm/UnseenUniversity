"""
TemplateShim — lifecycle shim starter.

Copy this alongside TemplateDevice. Rename to YourAgentShim.
Fill in start/stop/restart to launch and manage your agent process.

Common pattern: use subprocess.Popen to launch a worker, store the handle,
and check returncode in self_test.
"""

from __future__ import annotations

from unseen_university.shim import BaseShim


class TemplateShim(BaseShim):
    # Replace with your device's actual ID
    _device_id = "template"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        # Replace with your process launch logic, e.g.:
        # self._proc = subprocess.Popen(["python", "-m", "your_agent"])
        # return self._proc.poll() is None
        return True

    def stop(self) -> bool:
        # Replace with graceful shutdown logic, e.g.:
        # if self._proc: self._proc.terminate(); self._proc.wait(timeout=10)
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        # Replace with a real health probe — HTTP ping, DB query, etc.
        return {"passed": True, "details": "stub — replace with real self-test"}

    def rollback(self) -> None:
        # Called when start() returns False. Undo any partial setup.
        # If start() didn't do anything, this is a no-op.
        self.stop()
