"""
InferenceShim — lifecycle management for the inference backend.

OpenRouter mode: no process to manage — shim verifies API key is set.
Ollama mode: manages the ollama serve process.

self_test() checks reachability without launching anything in OpenRouter mode.
In Ollama mode, self_test() starts a temporary server if needed.

Bus envelope types for callers that dispatch via InferenceDevice.dispatch():
  InferenceRequest  — what to send
  InferenceResponse — what you get back
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from unseen_university.shim import BaseShim


@dataclass
class InferenceRequest:
    """Bus envelope for an inference dispatch call."""

    messages: list[dict]
    model: str = "openai/gpt-4o-mini"
    max_tokens: int = 4096
    temperature: float = 0.0
    system: str = ""
    timeout: int = 60
    extra: dict = field(default_factory=dict)
    # Agent context for budget ledger attribution and enforcement.
    agent_id: str = ""
    instance_id: str = ""
    coa_id: str = ""
    session_id: str = ""


@dataclass
class InferenceResponse:
    """Bus envelope returned by InferenceDevice.dispatch()."""

    text: str
    model: str = ""
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0
    elapsed_ms: int = 0
    raw: dict = field(default_factory=dict)


log = logging.getLogger(__name__)

_MODE = os.environ.get("INFERENCE_MODE", "openrouter")
_OLLAMA_PORT = 11434


def _ollama_port_responds(timeout: float = 5.0) -> bool:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", _OLLAMA_PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


class InferenceShim(BaseShim):
    """
    Manages the inference backend.

    For OpenRouter: verifies OPENROUTER_API_KEY is present.
    For Ollama: manages the `ollama serve` process lifecycle.
    """

    def __init__(self, mode: str = _MODE) -> None:
        self._mode = mode
        self._process: subprocess.Popen | None = None

    @property
    def device_id(self) -> str:
        return "inference"

    def start(self) -> bool:
        if self._mode == "openrouter":
            if not os.environ.get("OPENROUTER_API_KEY"):
                log.error(
                    "OPENROUTER_API_KEY not set — OpenRouter inference unavailable"
                )
                return False
            log.info("Inference (openrouter): API key present")
            return True

        # Ollama mode
        if self._process is not None and self._process.poll() is None:
            log.info("Ollama already running (pid=%d)", self._process.pid)
            return True

        ollama = shutil.which("ollama")
        if ollama is None:
            log.error("ollama binary not found in PATH")
            return False

        try:
            self._process = subprocess.Popen(
                [ollama, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("Failed to start ollama: %s", exc)
            return False

        if not _ollama_port_responds():
            log.error("ollama launched but port %d never responded", _OLLAMA_PORT)
            self._process.kill()
            self._process = None
            return False

        log.info("ollama started (pid=%d)", self._process.pid)
        return True

    def stop(self) -> bool:
        if self._mode == "openrouter":
            return True
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
        log.info("ollama stopped (pid=%d)", self._process.pid)
        self._process = None
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        if self._mode == "openrouter":
            has_key = bool(os.environ.get("OPENROUTER_API_KEY"))
            return {
                "passed": has_key,
                "details": (
                    "OPENROUTER_API_KEY present"
                    if has_key
                    else "OPENROUTER_API_KEY not set"
                ),
            }
        # Ollama: just check port reachability
        if _ollama_port_responds(timeout=2.0):
            return {
                "passed": True,
                "details": f"Ollama responding on port {_OLLAMA_PORT}",
            }
        ollama = shutil.which("ollama")
        if ollama:
            return {
                "passed": True,
                "details": f"ollama binary found at {ollama!r} (not running; call start() first)",
            }
        return {
            "passed": False,
            "details": "ollama binary not found and port not responding",
        }

    def rollback(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except Exception:
                pass
            self._process = None
        log.info("InferenceShim rollback complete")
