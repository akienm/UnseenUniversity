"""
sources.py — Provider abstraction for the inference proxy mini-rack.

Each Source wraps one inference backend and knows how to:
  - ping() itself for health monitoring
  - call() with an InferenceRequest, returning a raw response dict

SourceRegistry holds all configured sources and is queried by the RulesEngine.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devices.inference.shim import InferenceRequest

log = logging.getLogger(__name__)


@dataclass
class Source:
    """Base provider. Subclass and implement ping() + call()."""

    name: str
    available: bool = True

    def ping(self) -> bool:
        raise NotImplementedError

    def call(self, req: "InferenceRequest") -> dict:
        raise NotImplementedError

    def check_and_update(self) -> bool:
        """Ping and update self.available. Returns new availability."""
        self.available = self.ping()
        return self.available


class OpenRouterSource(Source):
    """OpenRouter — cloud inference for all OR-hosted models."""

    def __init__(self) -> None:
        super().__init__(name="openrouter")

    def _api_key(self) -> str:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        return key

    def ping(self) -> bool:
        try:
            with socket.create_connection(("openrouter.ai", 443), timeout=3):
                return True
        except OSError:
            return False

    def _is_cacheable(self, model_id: str) -> bool:
        """Return True if this model supports OR prefix caching."""
        try:
            from devices.inference.models_registry import default_registry

            spec = default_registry().get(model_id)
            return spec.cacheable if spec else False
        except Exception:
            return False

    def call(self, req: "InferenceRequest") -> dict:
        if req.system:
            if self._is_cacheable(req.model):
                # Wrap system prompt as a content-array with cache_control so OR
                # caches this prefix. Cache hits appear as cache_read_input_tokens
                # in the response usage — saving up to 90% on repeat iterations.
                sys_msg = {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": req.system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            else:
                sys_msg = {"role": "system", "content": req.system}
            messages = [sys_msg] + req.messages
        else:
            messages = req.messages

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
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/UnseenUniversity",
                "X-Title": "agent-datacenter-inference",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                result = json.loads(resp.read())
            usage = result.get("usage", {})
            cache_read = usage.get("cache_read_input_tokens", 0)
            if cache_read:
                log.debug(
                    "OR cache hit: model=%s cache_read=%d prompt=%d",
                    req.model,
                    cache_read,
                    usage.get("prompt_tokens", 0),
                )
            return result
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"OpenRouter {exc.code}: {err_body}") from exc


class OllamaSource(Source):
    """Local Ollama server."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        super().__init__(name="ollama")
        self.base_url = base_url.rstrip("/")

    def ping(self) -> bool:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 11434
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    def call(self, req: "InferenceRequest") -> dict:
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
            f"{self.base_url}/api/chat",
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


class AnthropicSource(Source):
    """Direct Anthropic API — used for designer-tier (Claude Max plan sessions)."""

    BASE_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self) -> None:
        super().__init__(name="anthropic")

    def _api_key(self) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return key

    def ping(self) -> bool:
        try:
            with socket.create_connection(("api.anthropic.com", 443), timeout=3):
                return True
        except OSError:
            return False

    def call(self, req: "InferenceRequest") -> dict:
        system_blocks = [{"type": "text", "text": req.system}] if req.system else []
        payload: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": req.messages,
        }
        if system_blocks:
            payload["system"] = system_blocks
        payload.update(req.extra)
        body = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            self.BASE_URL,
            data=body,
            headers={
                "x-api-key": self._api_key(),
                "anthropic-version": self.API_VERSION,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                raw = json.loads(resp.read())
            # Normalise to OpenAI-compatible shape so _parse_response works
            content = raw.get("content", [])
            text = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
            usage = raw.get("usage", {})
            return {
                "choices": [
                    {
                        "message": {"content": text},
                        "finish_reason": raw.get("stop_reason", "stop"),
                    }
                ],
                "model": raw.get("model", req.model),
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                },
            }
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"Anthropic {exc.code}: {err_body}") from exc


# ── Registry ──────────────────────────────────────────────────────────────────


class SourceRegistry:
    """Holds all configured sources. RulesEngine queries this for routing."""

    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        self._sources[source.name] = source
        log.info("SourceRegistry: registered source %r", source.name)

    def get(self, name: str) -> Source | None:
        return self._sources.get(name)

    def all_available(self) -> list[Source]:
        return [s for s in self._sources.values() if s.available]

    def all(self) -> list[Source]:
        return list(self._sources.values())


def default_registry() -> SourceRegistry:
    """Build the standard source registry from env."""
    reg = SourceRegistry()
    reg.register(OpenRouterSource())
    reg.register(
        OllamaSource(
            base_url=os.environ.get("INFERENCE_ENDPOINT", "http://127.0.0.1:11434")
        )
    )
    reg.register(AnthropicSource())
    return reg
