"""
InferenceDevice — rack device owning LLM inference dispatch.

Supports two modes:
  openrouter   — proxied LLM inference via openrouter.ai (requires OR API key)
  ollama       — local Ollama server (no key required)

Mode is set via INFERENCE_MODE env var (default: openrouter).
Endpoint URL is set via INFERENCE_ENDPOINT env var.

Primary entry point: dispatch(InferenceRequest) -> InferenceResponse.
This is a thin HTTP client — no tool-use loops, no budget management, no
prompt assembly. Callers handle prompt shape; this device handles transport.

Health + comms:// registration are the secondary role (rack-visible contract).
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from agent_datacenter.device import BaseDevice, INTERFACE_VERSION
from devices.inference.shim import InferenceRequest, InferenceResponse

_START_TIME = time.time()
_MODE = os.environ.get("INFERENCE_MODE", "openrouter")
_OPENROUTER_ENDPOINT = "openrouter.ai"
_OLLAMA_DEFAULT = "http://127.0.0.1:11434"
_ENDPOINT = os.environ.get("INFERENCE_ENDPOINT", "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openrouter_reachable() -> bool:
    try:
        with socket.create_connection((_OPENROUTER_ENDPOINT, 443), timeout=3):
            return True
    except OSError:
        return False


def _ollama_reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class InferenceDevice(BaseDevice):
    """
    Device representing the inference backend (OpenRouter or Ollama).

    Primarily provides health reporting and comms:// registration.
    Actual inference calls go through the inference library directly —
    this device is the rack's view of inference availability.
    """

    DEVICE_ID = "inference"

    def __init__(
        self,
        mode: str = _MODE,
        endpoint: str = _ENDPOINT,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._endpoint = endpoint or (_OLLAMA_DEFAULT if mode == "ollama" else "")
        self._blocked = False
        self._block_reason = ""

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": f"Inference ({self._mode})",
            "version": "0.1.0",
            "purpose": f"LLM inference via {self._mode}",
            "mode": self._mode,
            "endpoint": self._endpoint or "(auto)",
        }

    def requirements(self) -> dict:
        if self._mode == "openrouter":
            return {
                "deps": [],
                "system": ["OPENROUTER_API_KEY env var", "internet access"],
            }
        return {
            "deps": [],
            "system": ["ollama running on localhost:11434"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["inference_response"],
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
        if self._mode == "openrouter":
            reachable = _openrouter_reachable()
            return {
                "status": "healthy" if reachable else "unhealthy",
                "detail": (
                    "openrouter.ai reachable"
                    if reachable
                    else "openrouter.ai unreachable"
                ),
                "checked_at": _now(),
            }
        reachable = _ollama_reachable(self._endpoint)
        return {
            "status": "healthy" if reachable else "unhealthy",
            "detail": f"Ollama {'responding' if reachable else 'not responding'} at {self._endpoint}",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        if self._mode == "openrouter":
            if not os.environ.get("OPENROUTER_API_KEY"):
                return ["OPENROUTER_API_KEY not set"]
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "openrouter.ai" if self._mode == "openrouter" else "localhost",
            "endpoint": self._endpoint,
            "mode": self._mode,
            "launch_command": (
                "InferenceShim().start()" if self._mode == "ollama" else "n/a"
            ),
        }

    def restart(self) -> None:
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

    # ── Inference dispatch ────────────────────────────────────────────────────

    def dispatch(self, request: InferenceRequest) -> InferenceResponse:
        """Send an inference request and return the response.

        Uses the device's configured mode (openrouter or ollama). Raises
        RuntimeError on API error or if the device is blocked.
        """
        if self._blocked:
            raise RuntimeError(f"InferenceDevice blocked: {self._block_reason}")
        if self._mode == "openrouter":
            from devices.inference.budget_gate import check_balance, record_spend

            ok, msg = check_balance()
            if not ok:
                raise RuntimeError(f"OR budget gate: {msg}")
        t0 = time.time()
        if self._mode == "openrouter":
            raw = self._or_call(request)
        else:
            raw = self._ollama_call(request)
        elapsed_ms = round((time.time() - t0) * 1000)
        resp = _parse_response(raw, elapsed_ms=elapsed_ms)
        if self._mode == "openrouter":
            record_spend(
                resp.model or request.model, resp.input_tokens, resp.output_tokens
            )
        return resp

    def _or_call(self, req: InferenceRequest) -> dict:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        messages = (
            [{"role": "system", "content": req.system}] + req.messages
            if req.system
            else req.messages
        )
        payload: dict = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        payload.update(req.extra)
        body = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/TheIgors",
                "X-Title": "agent-datacenter-inference",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"OpenRouter {exc.code}: {err_body}") from exc

    def _ollama_call(self, req: InferenceRequest) -> dict:
        base = (self._endpoint or _OLLAMA_DEFAULT).rstrip("/")
        messages = (
            [{"role": "system", "content": req.system}] + req.messages
            if req.system
            else req.messages
        )
        payload = {
            "model": req.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": req.temperature, "num_predict": req.max_tokens},
        }
        payload.update(req.extra)
        body = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            f"{base}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"Ollama {exc.code}: {err_body}") from exc


def _parse_response(raw: dict, elapsed_ms: int = 0) -> InferenceResponse:
    """Parse an OpenAI-compatible or Ollama response into InferenceResponse."""
    # OpenAI-compatible (OpenRouter + Ollama /v1/)
    choices = raw.get("choices")
    if choices:
        choice = choices[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        usage = raw.get("usage") or {}
        return InferenceResponse(
            text=text,
            model=raw.get("model", ""),
            finish_reason=finish_reason,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            elapsed_ms=elapsed_ms,
            raw=raw,
        )
    # Ollama /api/chat native format
    msg = raw.get("message") or {}
    text = msg.get("content") or ""
    done_reason = raw.get("done_reason") or ("stop" if raw.get("done") else "")
    return InferenceResponse(
        text=text,
        model=raw.get("model", ""),
        finish_reason=done_reason or "stop",
        input_tokens=raw.get("prompt_eval_count", 0),
        output_tokens=raw.get("eval_count", 0),
        elapsed_ms=elapsed_ms,
        raw=raw,
    )
