"""SummarizerShim — lifecycle management for the summarizer HTTP server."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_PORT = int(os.environ.get("SUMMARIZER_PORT", "8085"))


def _port_responds(port: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class SummarizerShim(BaseShim):
    """Manages the summarizer device process."""

    _device_id = "summarizer"

    def __init__(self, port: int = _PORT) -> None:
        self._port = port
        self._process: subprocess.Popen | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            log.info("summarizer already running (pid=%d)", self._process.pid)
            return True
        try:
            self._process = subprocess.Popen(
                ["python", "-m", "devices.summarizer.device"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "SUMMARIZER_PORT": str(self._port)},
            )
        except OSError as exc:
            log.error("summarizer start failed: %s", exc)
            return False

        if not _port_responds(self._port):
            log.error("summarizer launched but port %d never responded", self._port)
            self._process.kill()
            self._process = None
            return False

        log.info("summarizer started (pid=%d, port=%d)", self._process.pid, self._port)
        return True

    def stop(self) -> bool:
        if self._process is None:
            return True
        if self._process.poll() is not None:
            self._process = None
            return True
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        log.info("summarizer stopped")
        self._process = None
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        import urllib.request

        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self._port}/api/health", timeout=5
            ) as resp:
                import json

                data = json.loads(resp.read())
                return {"passed": True, "details": data.get("detail", "ok")}
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except Exception:
                pass
            self._process = None
