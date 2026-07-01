"""
sources.py — Provider abstraction for the inference proxy mini-rack.

Each Source wraps one inference backend and knows how to:
  - ping() itself for health monitoring
  - call() with an InferenceRequest, returning a raw response dict

SourceRegistry holds all configured sources and is queried by the RulesEngine.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from unseen_university.devices.inference.shim import InferenceRequest

log = logging.getLogger(__name__)


class OllamaCloudFatalError(RuntimeError):
    """Raised by OllamaCloudSource when the failure is non-retriable.

    Distinct from plain RuntimeError so callers can catch it separately and
    halt the tier cascade rather than falling through to expensive OR models.
    Covers: 4xx HTTP errors, exhausted retries (503 spam), network errors.
    """


_AKIEN_CREDS_FILE = os.path.expanduser(
    "~/.unseen_university/akien/akien.credentials.cfg"
)


def _read_akien_cred(key: str, owner: str = "akien") -> str:
    """Read a credential: vault first (scoped), flat file fallback.

    Vault is tried first — returns '' on any error or when vault is unavailable,
    allowing the flat-file fallback to serve credentials during migration.
    owner defaults to 'akien' but can be overridden (vault scoping).
    """
    try:
        from unseen_university.devices.vault.client import get_credential
        val = get_credential("inference", owner, key)
        if val:
            return val
    except Exception:
        pass
    # Flat-file fallback — preserved for migration period and cold-start before vault is up
    try:
        for line in open(_AKIEN_CREDS_FILE).read().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


@dataclass
class Source:
    """Base provider. Subclass and implement ping() + call()."""

    name: str
    available: bool = True
    # "flat_rate" = subscription (prefer over usage-based); "usage_based" = pay-per-token
    billing_type: str = "usage_based"
    # Cost axis for the cost-optimizing router (D-inference-cost-optimizing-router):
    # ordered cheap→dear per routing_buckets.COST_CLASSES. Corrects the inverted binary
    # billing_type — owned-local hardware is genuinely cheaper than a metered subscription,
    # a distinction per-token cost cannot make (both bill $0/token). The selector reads
    # this (increment 2); billing_type stays the live sort key until then.
    cost_class: str = "token_direct"
    # Speed axis: how fast this source answers RIGHT NOW (routing_buckets.TIME_BUCKETS,
    # interactive|minutes|overnight). An eligibility filter, not a static label —
    # increment 4 re-measures it live so a box that got faster (Hex) is promoted, not
    # pinned by a stale "ollama = slow" label. Mutable per-instance for that reason.
    time_bucket: str = "interactive"
    # On-box inference (no network hop, no paid token cost) vs networked cloud.
    # Distinct from billing_type: a cloud source can be flat_rate (subscription)
    # yet still NOT local — callers needing local-vs-cloud must read this, not
    # billing_type. ClassVar so it stays off the dataclass __init__ (subclasses
    # override the class attribute; no constructor churn).
    is_local: ClassVar[bool] = False

    def ping(self) -> bool:
        raise NotImplementedError

    def call(self, req: "InferenceRequest") -> dict:
        raise NotImplementedError

    def check_and_update(self) -> bool:
        """Ping and update self.available. Returns new availability."""
        self.available = self.ping()
        return self.available

    def self_test(self) -> tuple[bool, str]:
        """Ping cheapest model with hello world; return (success, reason).

        Subclasses override to test their specific cheapest model.
        Base implementation just does ping().
        """
        success = self.ping()
        return success, "ping: " + ("ok" if success else "failed")

    def _classify_ping_failure(
        self,
        exc: BaseException,
        *,
        model_name: str = "",
        base_url: str = "",
        api_key: str = "",
        requested_context_window: int | None = None,
    ) -> dict:
        """Classify a ping/call failure and return enriched failure info.

        Returns::

            {
                'failure_category': 'model_not_found' | 'local_bug' | 'auth_error'
                                    | 'unreachable' | 'unknown',
                'alternatives': [list of model names],  # non-empty only for model_not_found
            }

        When failure_category is 'model_not_found', also logs at INFO::

            log.info('source %s model %s not found: alternatives=%s', ...)
        """
        from unseen_university.devices.inference.model_alternatives import ModelAlternativesClassifier

        classifier = ModelAlternativesClassifier(
            source_name=self.name,
            base_url=base_url or "",
            api_key=api_key or "",
            requested_context_window=requested_context_window,
        )
        failure_category, alternatives = classifier.classify(self.name, model_name, exc)

        if failure_category == "model_not_found":
            log.info(
                "source %s model %s not found: alternatives=%s",
                self.name,
                model_name,
                alternatives,
            )

        return {"failure_category": failure_category, "alternatives": alternatives}

    def _classify_ping_failure_simple(
        self,
        exc: BaseException,
    ) -> str:
        """Classify a ping failure and return a category.

        This method is called from subclass ping() except blocks to categorize
        the exception before returning False. It logs the failure category at INFO.

        Returns one of: 'local_bug', 'auth_error', 'unreachable', 'unknown'
        """
        from unseen_university.devices.inference.provider_health import ProviderHealthClassifier

        category = ProviderHealthClassifier.classify(self.name, exc)
        log.info('source %s unavailable: failure_category=%s', self.name, category)
        return category


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
        except OSError as exc:
            self._classify_ping_failure_simple(exc)
            return False

    def _is_cacheable(self, model_id: str) -> bool:
        """Return True if this model supports OR prefix caching."""
        try:
            from unseen_university.devices.inference.models_registry import default_registry

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

    is_local: ClassVar[bool] = True  # on-box; the only true-local source

    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        # owned_local: on-box hardware we own (Hex/igor cluster) — the cheapest rung,
        # cheaper than the ollama_cloud subscription despite both billing $0/token.
        super().__init__(name="ollama", cost_class="owned_local")
        self.base_url = base_url.rstrip("/")

    def ping(self) -> bool:
        """Honest liveness = dispatch-ability, not a bare socket.

        A hung or mis-served ollama holds the socket open while its HTTP API 404s or times
        out, so a socket connect alone reports a dead server as LIVE and the selector routes
        a call that then dies (T-inference-ollama-honest-liveness). Probe the API instead —
        GET /api/tags is cheap, read-only, and costs no generation: urlopen RAISES HTTPError
        on a 404 and URLError on a hang/refused, so only a real 200 gets through. A 200 with
        a NON-EMPTY model list means the server can actually serve; an empty list (nothing
        pulled) is not dispatchable. Fail-soft: any probe error → False (classified), never
        raises into the health loop. (Per-MODEL 'is THIS model pulled' is a finer,
        selector-level check — a separate follow-up.)
        """
        try:
            http_req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(http_req, timeout=3) as resp:
                data = json.loads(resp.read())
            return bool(data.get("models"))
        except Exception as exc:
            self._classify_ping_failure_simple(exc)
            return False

    def self_test(self) -> tuple[bool, str]:
        """Test by calling cheapest local Ollama model with hello world."""
        if not self.ping():
            return False, "ping failed"
        try:
            payload = {
                "model": "nemotron-mini",
                "messages": [{"role": "user", "content": "hello world"}],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 10},
            }
            body = json.dumps(payload).encode()
            http_req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(http_req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("message") and result["message"].get("content"):
                    return True, f"ok: {result['message']['content'][:50]}"
                return False, "empty response"
        except Exception as e:
            return False, f"error: {type(e).__name__}: {str(e)[:100]}"

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
        # Forward tool definitions so the model emits native tool_calls (Ollama /api/chat
        # supports `tools`). Without this, a tool-capable model (devstral) improvises the
        # call as prose and the ToolLoop never sees a tool_call — the local-Ollama
        # counterpart of the OllamaCloudSource tools-forwarding fix.
        if req.tools:
            payload["tools"] = req.tools
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
        # Check env first (ANTHROPIC_API_KEY, then REAL_ANTHROPIC_API_KEY)
        for var in ("ANTHROPIC_API_KEY", "REAL_ANTHROPIC_API_KEY"):
            key = os.environ.get(var, "").strip()
            if key:
                return key
        # Vault + flat-file fallback via shared helper
        for var in ("REAL_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
            key = _read_akien_cred(var)
            if key:
                return key
        raise RuntimeError("ANTHROPIC_API_KEY not set (checked env + akien.credentials.cfg)")

    def ping(self) -> bool:
        # No key → source unavailable regardless of connectivity
        try:
            if not self._api_key():
                return False
        except RuntimeError:
            return False
        try:
            with socket.create_connection(("api.anthropic.com", 443), timeout=3):
                return True
        except OSError as exc:
            self._classify_ping_failure_simple(exc)
            return False

    @staticmethod
    def _convert_tools(openai_tools: list) -> list:
        """Convert OpenAI tool definitions to Anthropic format."""
        out = []
        for t in openai_tools:
            fn = t.get("function", {})
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

    @staticmethod
    def _convert_messages(openai_msgs: list) -> list:
        """Convert OpenAI-format messages (including tool_calls/tool roles) to Anthropic format.

        Consecutive tool-result messages are batched into a single user message with
        multiple tool_result content blocks, as required by the Anthropic API.
        """
        out = []
        i = 0
        while i < len(openai_msgs):
            msg = openai_msgs[i]
            role = msg.get("role", "")

            if role == "tool":
                # Batch all consecutive tool messages into one user message
                tool_results = []
                while i < len(openai_msgs) and openai_msgs[i].get("role") == "tool":
                    m = openai_msgs[i]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    })
                    i += 1
                out.append({"role": "user", "content": tool_results})
                continue

            if role == "assistant":
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    try:
                        inp = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        inp = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": inp,
                    })
                if content_blocks:
                    out.append({"role": "assistant", "content": content_blocks})
                i += 1
                continue

            # user / system — pass through as text content
            out.append({"role": role, "content": msg.get("content", "")})
            i += 1
        return out

    def call(self, req: "InferenceRequest") -> dict:
        # System block with cache_control — marks prefix for caching on long contexts.
        system_blocks = []
        if req.system:
            system_blocks = [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ]

        anthropic_messages = self._convert_messages(req.messages)

        payload: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": anthropic_messages,
        }
        if system_blocks:
            payload["system"] = system_blocks
        if req.tools:
            payload["tools"] = self._convert_tools(req.tools)
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
            content_blocks = raw.get("content", [])
            text = "".join(
                b.get("text", "") for b in content_blocks if b.get("type") == "text"
            )
            # Convert Anthropic tool_use blocks → OpenAI tool_calls format
            tool_calls = None
            tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
            if tool_use_blocks:
                tool_calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    }
                    for b in tool_use_blocks
                ]

            usage = raw.get("usage", {})
            cache_read = usage.get("cache_read_input_tokens", 0)
            if cache_read:
                log.debug(
                    "Anthropic cache hit: model=%s cache_read=%d prompt=%d",
                    req.model, cache_read, usage.get("input_tokens", 0),
                )
            stop_reason = raw.get("stop_reason", "stop")
            finish_reason = "tool_calls" if tool_calls else stop_reason
            msg_out: dict = {"content": text}
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            return {
                "choices": [{"message": msg_out, "finish_reason": finish_reason}],
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

    _RATE_LIMIT_TTL = 60  # seconds to mark unavailable after a 429

    def __init__(self, free_tier: bool = False) -> None:
        name = "google_free" if free_tier else "google"
        super().__init__(name=name)
        self.free_tier = free_tier
        if free_tier:
            self.billing_type = "flat_rate"
            # free_throttled: $0 but rate-limited/external (~15 RPM) — cheaper than a
            # paid subscription, dearer than owned-local hardware.
            self.cost_class = "free_throttled"
        self._rate_limited_until: float = 0.0

    _KEY_ALIASES = ("GOOGLE_AI_STUDIO_API_KEY", "GOOGLE_STUDIO_API_KEY", "GEMINI_API_KEY")

    def _gemini_key(self) -> str:
        """Resolve the Google API key: env first, then akien.credentials.cfg.

        Mirrors OllamaCloudSource — the key (GOOGLE_STUDIO_API_KEY lives in the
        credentials file, not the daemon env) resolves without an env export.
        Returns '' when no key is found. Single source of truth so _api_key and
        ping() never drift on what counts as "have a key".
        """
        for var in self._KEY_ALIASES:
            key = os.environ.get(var, "").strip()
            if key:
                return key
        for var in self._KEY_ALIASES:
            key = _read_akien_cred(var).strip()
            if key:
                return key
        return ""

    def _api_key(self) -> str:
        key = self._gemini_key()
        if key:
            return key
        raise RuntimeError(
            "Google API key not set — set GOOGLE_AI_STUDIO_API_KEY, GOOGLE_STUDIO_API_KEY, "
            "or GEMINI_API_KEY (env or akien.credentials.cfg)"
        )

    def _model_name(self, model_id: str) -> str:
        """Strip 'google/' prefix if present; return bare model name for URL."""
        return model_id.removeprefix("google/")

    def ping(self) -> bool:
        import time
        # No key → source is functionally unavailable regardless of connectivity
        if not self._gemini_key():
            return False
        if time.time() < self._rate_limited_until:
            return False
        try:
            with socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=3):
                return True
        except OSError as exc:
            self._classify_ping_failure_simple(exc)
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
            if exc.code == 429:
                import time
                self._rate_limited_until = time.time() + self._RATE_LIMIT_TTL
                self.available = False
                log.warning(
                    "GoogleSource %s: 429 rate-limited — marking unavailable for %ds",
                    self.name,
                    self._RATE_LIMIT_TTL,
                )
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

    Uses the OpenAI-compatible endpoint at ollama.com (or OLLAMA_CLOUD_ENDPOINT).
    (Note: api.ollama.com 301-redirects to ollama.com; urllib converts POST→GET on 301s)
    Auth via OLLAMA_PRO_API_KEY (alias: OLLAMA_API_KEY). Disabled (available=False) when key is not set.

    billing_type="flat_rate": preferred over usage-based sources when routing.
    """

    DEFAULT_ENDPOINT = "https://ollama.com/v1/chat/completions"

    def __init__(self) -> None:
        api_key = (
            os.environ.get("OLLAMA_PRO_API_KEY", "").strip()
            or os.environ.get("OLLAMA_API_KEY", "").strip()
            or _read_akien_cred("OLLAMA_API_KEY")
            or _read_akien_cred("OLLAMA_PRO_API_KEY")
        )
        endpoint = os.environ.get(
            "OLLAMA_CLOUD_ENDPOINT", self.DEFAULT_ENDPOINT
        )
        super().__init__(
            name="ollama_cloud",
            available=bool(api_key),
            billing_type="flat_rate",
            # subscription: fixed $20/mo sub + metered usage caps — NOT owned-local.
            # This is the taxonomy fix: the cloud account was mislabelled cheapest.
            cost_class="subscription",
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
            or _read_akien_cred("OLLAMA_API_KEY")
            or _read_akien_cred("OLLAMA_PRO_API_KEY")
        ):
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self._endpoint)
            host = parsed.hostname or "api.ollama.com"
            port = parsed.port or 443
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError as exc:
            self._classify_ping_failure_simple(exc)
            return False

    def _get_available_models(self) -> list[str]:
        """Fetch list of available models from Ollama Cloud."""
        try:
            models_endpoint = self._endpoint.replace("/v1/chat/completions", "/v1/models")
            http_req = urllib.request.Request(
                models_endpoint,
                headers={"Authorization": f"Bearer {self._api_key()}"},
            )
            with urllib.request.urlopen(http_req, timeout=5) as resp:
                data = json.loads(resp.read())
                return [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            return []

    def _fuzzy_match_model(self, requested: str) -> str | None:
        """Find best matching model name using fuzzy matching.

        If exact match exists, return it.
        Otherwise find the closest match with > 0.6 similarity.
        """
        available = self._get_available_models()
        if not available:
            return None

        # Exact match
        if requested in available:
            return requested

        # Fuzzy match — find closest
        matches = difflib.get_close_matches(requested, available, n=1, cutoff=0.6)
        return matches[0] if matches else None

    def self_test(self) -> tuple[bool, str]:
        """Test by calling a small available model, using full retry logic."""
        if not self.ping():
            return False, "ping failed"

        # Try to find a small model
        test_model = self._fuzzy_match_model("ministral-3:3b")
        if not test_model:
            # Fallback: get any model
            available = self._get_available_models()
            test_model = available[0] if available else "ministral-3:3b"

        try:
            # Create a minimal InferenceRequest to test the full call() path with retries
            from unseen_university.devices.inference.shim import InferenceRequest

            req = InferenceRequest(
                model=test_model,  # SANCTIONED pin — self-test targets one small model
                pin_reason="inference_test",
                messages=[{"role": "user", "content": "hello world"}],
                system="",
                max_tokens=10,
                temperature=0,
                timeout=30,
                task_class="self_test",
                agent_id="source_health_check",
            )
            result = self.call(req)
            if result.get("choices") and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")[:40]
                return True, f"ok ({test_model}): {content}"
            return False, f"empty response ({test_model})"
        except Exception as e:
            return False, f"error ({test_model}): {type(e).__name__}: {str(e)[:80]}"

    def call(self, req: "InferenceRequest") -> dict:
        """Call Ollama Cloud with aggressive retry + exponential backoff.

        Ollama Cloud frequently returns 503/502/empty responses without proper
        Retry-After headers. Retry up to 3 times with exponential backoff before
        raising. Caller (HealthMonitor/RulesEngine) will trigger fallthrough.
        """
        import time

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

        max_retries = 3
        delay = 2  # Start at 2 seconds
        last_exc = None

        for attempt in range(max_retries):
            try:
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
                with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                    result = json.loads(resp.read())
                    # Validate response has actual content
                    if result.get("choices") and len(result["choices"]) > 0:
                        if attempt > 0:
                            log.info(
                                "OllamaCloud succeeded after %d retries (model=%s)",
                                attempt,
                                req.model,
                            )
                        return result
                    else:
                        # Empty response — count as retriable error
                        log.warning(
                            "OllamaCloud attempt %d/%d: empty choices (model=%s)",
                            attempt + 1,
                            max_retries,
                            req.model,
                        )
                        last_exc = RuntimeError("Empty response from OllamaCloud")
                        if attempt < max_retries - 1:
                            time.sleep(delay)
                            delay *= 2
                        continue

            except urllib.error.HTTPError as exc:
                status_code = exc.code
                # 503/502 are retriable; all others are fatal — don't fall through to OR
                if status_code in (503, 502) and attempt < max_retries - 1:
                    log.warning(
                        "OllamaCloud attempt %d/%d: HTTP %d (model=%s), backing off %ds",
                        attempt + 1,
                        max_retries,
                        status_code,
                        req.model,
                        delay,
                    )
                    last_exc = exc
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    # Non-retriable (4xx, or 503 on final attempt) — stop, no OR fallthrough
                    err_body = exc.read().decode()[:200]
                    log.error(
                        "OllamaCloud %s HTTP %d (model=%s): %s",
                        "final attempt" if attempt == max_retries - 1 else f"attempt {attempt + 1}",
                        status_code,
                        req.model,
                        err_body,
                    )
                    raise OllamaCloudFatalError(
                        f"OllamaCloud {status_code}: {err_body}"
                    ) from exc

            except urllib.error.URLError as exc:
                # Network error (DNS, connection refused, etc.)
                if attempt < max_retries - 1:
                    log.warning(
                        "OllamaCloud attempt %d/%d: network error (model=%s), backing off %ds: %s",
                        attempt + 1,
                        max_retries,
                        req.model,
                        delay,
                        str(exc)[:100],
                    )
                    last_exc = exc
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    log.error(
                        "OllamaCloud final attempt: network error (model=%s): %s",
                        req.model,
                        str(exc)[:100],
                    )
                    raise OllamaCloudFatalError(
                        f"OllamaCloud network error: {str(exc)[:100]}"
                    ) from exc

        # Retries exhausted — stop, no OR fallthrough
        if last_exc:
            raise OllamaCloudFatalError(
                f"OllamaCloud failed after {max_retries} retries (model={req.model})"
            ) from last_exc
        raise OllamaCloudFatalError(
            f"OllamaCloud failed after {max_retries} retries (model={req.model})"
        )


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
    # TEMPORARY: Ollama-only test (2026-06-12) — small ticket validation
    # reg.register(GoogleSource(free_tier=False))  # google — disabled for test
    # reg.register(OpenRouterSource())             # openrouter — DISABLED for Ollama-only test
    # reg.register(AnthropicSource())              # anthropic direct API — stay disabled
    return reg
