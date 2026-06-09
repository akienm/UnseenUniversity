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
    # "flat_rate" = subscription (prefer over usage-based); "usage_based" = pay-per-token
    billing_type: str = "usage_based"

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
        if req.tools:
            payload["tools"] = req.tools
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
    """Direct Anthropic API — used for designer-tier (Claude API sessions)."""

    BASE_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    # Prompt caching beta: cache_control on system + first user turn saves up to 90%.
    BETA_HEADERS = "prompt-caching-2024-07-31"

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
        # System block with cache_control — marks prefix for caching on long contexts.
        if req.system:
            system_blocks = [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_blocks = []
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
                "anthropic-beta": self.BETA_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                raw = json.loads(resp.read())
            content = raw.get("content", [])
            text = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
            usage = raw.get("usage", {})
            cache_read = usage.get("cache_read_input_tokens", 0)
            if cache_read:
                log.debug(
                    "Anthropic cache hit: model=%s cache_read=%d prompt=%d",
                    req.model, cache_read, usage.get("input_tokens", 0),
                )
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
                    "cache_read_input_tokens": cache_read,
                },
            }
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"Anthropic {exc.code}: {err_body}") from exc


class GoogleSource(Source):
    """Native Google AI Studio / Gemini API.

    Bypasses OpenRouter to retain:
    - Automatic 75% prompt caching discount on payloads > 32k tokens
    - Free tier safety valve (free_tier=True) for boilerplate / public tasks
    - Direct connection for lowest latency on GoogleSecretary BPA loops

    Model IDs use the canonical Google format (e.g. 'gemini-2.0-flash'),
    not the OpenRouter namespaced form ('google/gemini-2.0-flash').

    Auth: GOOGLE_AI_STUDIO_API_KEY env var (aliases: GOOGLE_STUDIO_API_KEY, GEMINI_API_KEY).
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, free_tier: bool = False) -> None:
        name = "google_free" if free_tier else "google"
        super().__init__(name=name)
        self.free_tier = free_tier
        if free_tier:
            self.billing_type = "flat_rate"

    def _api_key(self) -> str:
        for var in ("GOOGLE_AI_STUDIO_API_KEY", "GOOGLE_STUDIO_API_KEY", "GEMINI_API_KEY"):
            key = os.environ.get(var, "").strip()
            if key:
                return key
        raise RuntimeError(
            "Google API key not set — set GOOGLE_AI_STUDIO_API_KEY, GOOGLE_STUDIO_API_KEY, or GEMINI_API_KEY"
        )

    def _model_name(self, model_id: str) -> str:
        """Strip 'google/' prefix if present; return bare model name for URL."""
        return model_id.removeprefix("google/")

    def ping(self) -> bool:
        try:
            with socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=3):
                return True
        except OSError:
            return False

    def _to_google_messages(self, req: "InferenceRequest") -> tuple[list, dict | None]:
        """Convert OpenAI-format messages to Google contents format.

        Returns (contents, system_instruction_or_None).
        Google uses role="model" where OpenAI uses role="assistant".
        System messages are extracted and returned as system_instruction.
        """
        contents = []
        system_instruction = None

        for msg in req.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                # Accumulate system messages into system_instruction
                existing = system_instruction["parts"][0]["text"] if system_instruction else ""
                combined = f"{existing}\n{content}".strip() if existing else content
                system_instruction = {"parts": [{"text": combined}]}
            else:
                google_role = "model" if role == "assistant" else "user"
                contents.append({"role": google_role, "parts": [{"text": content}]})

        if req.system:
            existing = system_instruction["parts"][0]["text"] if system_instruction else ""
            combined = f"{req.system}\n{existing}".strip() if existing else req.system
            system_instruction = {"parts": [{"text": combined}]}

        return contents, system_instruction

    def call(self, req: "InferenceRequest") -> dict:
        model_name = self._model_name(req.model)
        url = f"{self.BASE_URL}/{model_name}:generateContent"

        contents, system_instruction = self._to_google_messages(req)

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": req.max_tokens,
                "temperature": req.temperature,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        body = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key(),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                raw = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"Google {exc.code}: {err_body}") from exc

        candidates = raw.get("candidates", [])
        text = ""
        finish_reason = "stop"
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            finish_reason = candidates[0].get("finishReason", "STOP").lower()

        usage_meta = raw.get("usageMetadata", {})
        cached_tokens = usage_meta.get("cachedContentTokenCount", 0)
        if cached_tokens:
            log.debug(
                "Google cache hit: model=%s cached_tokens=%d prompt=%d tier=%s",
                model_name, cached_tokens,
                usage_meta.get("promptTokenCount", 0),
                "free" if self.free_tier else "paid",
            )

        return {
            "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
            "model": model_name,
            "usage": {
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                "cached_content_token_count": cached_tokens,
            },
        }


class OllamaCloudSource(Source):
    """Ollama Pro cloud API — flat-rate subscription ($20/mo Pro plan).

    Uses the OpenAI-compatible endpoint at api.ollama.com (or OLLAMA_CLOUD_ENDPOINT).
    Auth via OLLAMA_PRO_API_KEY (alias: OLLAMA_API_KEY). Disabled (available=False) when key is not set.

    billing_type="flat_rate": preferred over usage-based sources when routing.
    """

    DEFAULT_ENDPOINT = "https://api.ollama.com/v1/chat/completions"

    def __init__(self) -> None:
        api_key = (
            os.environ.get("OLLAMA_PRO_API_KEY", "").strip()
            or os.environ.get("OLLAMA_API_KEY", "").strip()
        )
        endpoint = os.environ.get(
            "OLLAMA_CLOUD_ENDPOINT", self.DEFAULT_ENDPOINT
        )
        super().__init__(
            name="ollama_cloud",
            available=bool(api_key),
            billing_type="flat_rate",
        )
        self._api_key_val = api_key
        self._endpoint = endpoint

    def _api_key(self) -> str:
        key = (
            os.environ.get("OLLAMA_PRO_API_KEY", "").strip()
            or os.environ.get("OLLAMA_API_KEY", "").strip()
            or self._api_key_val
        )
        if not key:
            raise RuntimeError("OLLAMA_PRO_API_KEY or OLLAMA_API_KEY not set")
        return key

    def ping(self) -> bool:
        if not (
            os.environ.get("OLLAMA_PRO_API_KEY", "").strip()
            or os.environ.get("OLLAMA_API_KEY", "").strip()
        ):
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self._endpoint)
            host = parsed.hostname or "api.ollama.com"
            port = parsed.port or 443
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False

    def call(self, req: "InferenceRequest") -> dict:
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
        if req.tools:
            payload["tools"] = req.tools
        payload.update(req.extra)
        body = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode()[:400]
            raise RuntimeError(f"OllamaCloud {exc.code}: {err_body}") from exc


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
    """Build the standard source registry from env.

    Provider priority (cost-ascending, flat-rate preferred):
      1. ollama_cloud — Ollama Pro flat-rate ($20/mo); active when OLLAMA_PRO_API_KEY set
      2. ollama       — local, $0
      3. google_free  — Google AI Studio free tier, $0 (rate-limited)
      4. google       — Google AI Studio paid, ~$0.10-0.40/1M + 75% auto-cache
      5. openrouter   — cloud fallback for non-Google models
      6. anthropic    — direct Anthropic API with prompt caching

    Routing prefers flat_rate sources (ollama_cloud) over usage_based within
    the same tier — see RulesEngine.route().
    """
    reg = SourceRegistry()
    reg.register(OllamaCloudSource())
    reg.register(OllamaSource(
        base_url=os.environ.get("INFERENCE_ENDPOINT", "http://127.0.0.1:11434")
    ))
    reg.register(GoogleSource(free_tier=True))   # google_free
    reg.register(GoogleSource(free_tier=False))  # google
    reg.register(OpenRouterSource())
    reg.register(AnthropicSource())
    return reg
