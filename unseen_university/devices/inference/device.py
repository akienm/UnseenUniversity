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
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse
from unseen_university.devices.inference.sources import (
    SourceRegistry,
    default_registry as _default_sources,
)
from unseen_university.devices.inference.models_registry import (
    ModelsRegistry,
    default_registry as _default_models,
)
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.health_monitor import HealthMonitor
from unseen_university.devices.inference.resource_monitor import ResourceMonitor

log = logging.getLogger(__name__)

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
        sources: SourceRegistry | None = None,
        models: ModelsRegistry | None = None,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._endpoint = endpoint or (_OLLAMA_DEFAULT if mode == "ollama" else "")
        self._blocked = False
        self._block_reason = ""
        # Mini-rack: sources + models + rules + health
        self._sources = sources or _default_sources()
        self._models = models or _default_models()
        self._rules = RulesEngine(self._sources, self._models)
        self._health = HealthMonitor(self._sources)
        self._health.start()
        # Live re-measurement (T-router-live-resource-read): every dispatch feeds its
        # observed latency here, which re-derives the source's time_bucket so a box that
        # got faster/slower is promoted/demoted — the selector reads it on the next call.
        self._monitor = ResourceMonitor()

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
            "public_methods": ["dispatch", "capability_graph_query"],
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
            "pid": os.getpid(),
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

    def capability_graph_query(
        self,
        task_class: str = "",
        model: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Query model eval results from the capability graph.

        Returns rows from adc.model_eval_results, newest first.
        Returns [] when UU_HOME_DB_URL is absent or the table doesn't exist yet.
        """
        from unseen_university.devices.inference.capability_graph import query_results

        db_url = os.environ.get("UU_HOME_DB_URL", "")
        if not db_url:
            return []
        log.info(
            "capability_graph_query: task_class=%r model=%r limit=%d",
            task_class,
            model,
            limit,
        )
        return query_results(db_url, task_class=task_class, model=model, limit=limit)

    def dispatch(self, request: InferenceRequest) -> InferenceResponse:
        """Route and dispatch an inference request via the mini-rack rules engine.

        Dispatch order:
          1. Pattern intercept (Level 2) — check archivist.knowledge_patterns first.
             If a compiled habit matches, return cached response at $0.
          2. Rules engine → Source + Model selection (cost cascade)
          3. Cloud call

        task_class on the request determines which Source + Model is selected.
        Falls back to legacy _mode behaviour if rules engine yields no decision.
        Raises RuntimeError on API error or if the device is blocked.
        """
        if self._blocked:
            raise RuntimeError(f"InferenceDevice blocked: {self._block_reason}")

        # Interface logging — InferenceRequest is a bus envelope; log routing-relevant flags.
        log.info(
            "dispatch: task_class=%s foreground=%s escalation_hop=%d",
            request.task_class,
            request.foreground,
            request.escalation_hop,
        )

        # Tier escalation: enforce 2-hop ceiling and prepend attempt summary
        _MAX_ESCALATION_HOPS = 2
        if request.escalation_hop >= _MAX_ESCALATION_HOPS:
            raise RuntimeError(
                f"InferenceDevice: escalation ceiling reached "
                f"({request.escalation_hop}/{_MAX_ESCALATION_HOPS} hops) — "
                f"cannot escalate further"
            )
        if request.escalation_hop > 0 and request.prior_attempt:
            # Prepend structured handoff to system prompt
            attempt_summary = (
                f"\n\n## Prior attempt summary (hop {request.escalation_hop})\n"
                f"**What was tried:** {request.prior_attempt}\n"
                "**What now?**\n"
            )
            request = InferenceRequest(
                messages=request.messages,
                model=request.model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=(request.system or "") + attempt_summary,
                timeout=request.timeout,
                extra=request.extra,
                task_class=request.task_class,
                agent_id=request.agent_id,
                instance_id=request.instance_id,
                coa_id=request.coa_id,
                session_id=request.session_id,
                tools=request.tools,
                escalation_hop=request.escalation_hop,
                prior_attempt=request.prior_attempt,
                foreground=request.foreground,
            )
            log.info(
                "dispatch: tier-escalation hop=%d prior_summary_len=%d",
                request.escalation_hop,
                len(request.prior_attempt),
            )

        # Level 2: pattern intercept — $0 if matched
        try:
            from unseen_university.devices.inference.pattern_intercept import try_intercept
            cached = try_intercept(request)
            if cached is not None:
                return cached
        except Exception as exc:
            log.debug("dispatch: pattern intercept error (non-fatal): %s", exc)

        from unseen_university.devices.inference.budget_ledger import check_session_limit, debit

        # Route via rules engine
        # When model is explicitly set, find its source directly — skip rules engine model selection
        # so the caller's explicit choice is honored (e.g. Dick's tier cascade forcing haiku/sonnet).
        source = None
        provider_name = ""
        decision = None
        if request.model:
            spec = self._models.get(request.model)
            if spec is not None:
                src = self._sources.get(spec.source_name)
                if src is not None and src.available:
                    source = src
                    provider_name = src.name
                    log.info(
                        "dispatch: explicit model=%s → %s (source=%s)",
                        request.model, request.model, provider_name,
                    )
                    decision = None  # skip rules-engine path below
                else:
                    log.warning(
                        "dispatch: explicit model=%s source=%s unavailable — falling through to rules engine",
                        request.model, spec.source_name,
                    )
                    request = InferenceRequest(
                        messages=request.messages, model="",
                        max_tokens=request.max_tokens, temperature=request.temperature,
                        system=request.system, timeout=request.timeout, extra=request.extra,
                        task_class=request.task_class, agent_id=request.agent_id,
                        tools=request.tools, domain=request.domain,
                        session_id=request.session_id, foreground=request.foreground,
                    )
                    decision = self._rules.route(
                        task_class=request.task_class or "worker",
                        session_id=request.session_id,
                        foreground=request.foreground,
                        domain=request.domain,
                    )
            else:
                # Unknown model ID — converge on the tier router (same fall-through as
                # an unavailable explicit model). No hardcoded source: route() skips dead
                # sources and returns None only when nothing is available at all, which the
                # complete-inference-failure chokepoint below turns into a loud alarm.
                log.info(
                    "dispatch: unknown model=%s — routing by task_class=%s via rules engine",
                    request.model, request.task_class or "worker",
                )
                decision = self._rules.route(
                    task_class=request.task_class or "worker",
                    session_id=request.session_id,
                    foreground=request.foreground,
                    domain=request.domain,
                )
        else:
            decision = self._rules.route(
                task_class=request.task_class or "worker",
                session_id=request.session_id,
                foreground=request.foreground,
                domain=request.domain,
            )

        if decision is not None:
            # Resolve model_id for the request
            request = InferenceRequest(
                messages=request.messages,
                model=decision.model.model_id,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=request.system,
                timeout=request.timeout,
                extra=request.extra,
                task_class=request.task_class,
                agent_id=request.agent_id,
                instance_id=request.instance_id,
                coa_id=request.coa_id,
                session_id=request.session_id,
                tools=request.tools,
                escalation_hop=request.escalation_hop,
                prior_attempt=request.prior_attempt,
                foreground=request.foreground,
                domain=request.domain,
            )
            source = decision.source
            provider_name = source.name
            log.info(
                "dispatch: %s → %s/%s (rule=%s)",
                request.task_class,
                decision.model.model_id,
                provider_name,
                decision.rule_label,
            )
        elif not request.model:
            # No rules decision and no explicit model — last-ditch legacy _mode source.
            # The complete-inference-failure chokepoint below catches it when _mode is a
            # dead/disabled source (e.g. the now-disabled openrouter default).
            log.warning(
                "dispatch: rules engine returned no decision — trying legacy mode=%s",
                self._mode,
            )
            source = self._sources.get(self._mode)
            provider_name = self._mode

        # ── Complete inference failure chokepoint ──────────────────────────────
        # Every routing path lands here. If no live source resolved (route() exhausted
        # all tier candidates, or the legacy fallback is a dead/disabled source), this is
        # a complete inference failure: raise a LOUD system alarm (surfaces on the web
        # ALARMS PANEL + tmux nag) and return a clean error — never fall through to the
        # legacy _or_call/_ollama_call dead-ends. fatal=False so the device stays up.
        if source is None or not getattr(source, "available", True):
            from unseen_university import system_alarms

            tc = request.task_class or "worker"
            system_alarms.raise_alarm(
                signature=f"no-provider:{tc}",
                caller=request.agent_id or "inference.device",
                message=(
                    f"complete inference failure — no live source for "
                    f"task_class={tc!r} model={request.model!r}"
                ),
                fatal=False,
            )
            log.error(
                "dispatch: complete inference failure — no live source for task_class=%s model=%s",
                tc, request.model,
            )
            return InferenceResponse(
                text=f"[InferenceDevice: no live inference source for task_class={tc}]",
                finish_reason="error",
                source_kind="none",
            )

        # OR-specific pre-call gates
        if provider_name == "openrouter":
            from unseen_university.devices.inference.budget_gate import check_balance, record_spend

            ok, msg = check_balance()
            if not ok:
                raise RuntimeError(f"OR budget gate: {msg}")
            if request.agent_id and request.session_id:
                ok, msg = check_session_limit(
                    request.agent_id, request.session_id, provider_name
                )
                if not ok:
                    raise RuntimeError(f"budget limit: {msg}")

        # source is guaranteed non-None and available past the chokepoint above.
        t0 = time.time()
        raw = source.call(request)
        elapsed_ms = round((time.time() - t0) * 1000)
        # Feed the observed latency into live re-measurement (increment 4).
        self._monitor.record(source, elapsed_ms / 1000.0)

        resp = _parse_response(raw, elapsed_ms=elapsed_ms)

        if provider_name == "openrouter":
            from unseen_university.devices.inference.budget_gate import record_spend

            record_spend(
                resp.model or request.model, resp.input_tokens, resp.output_tokens
            )
            cost_usd = resp.cost_estimate if resp.cost_estimate > 0 else None
        elif decision is not None and (resp.input_tokens or resp.output_tokens):
            cost_usd = decision.model.cost_estimate(
                input_tokens=resp.input_tokens or 0,
                output_tokens=resp.output_tokens or 0,
            )
        else:
            cost_usd = 0.0

        debit(
            agent_id=request.agent_id,
            instance_id=request.instance_id,
            coa_id=request.coa_id,
            session_id=request.session_id,
            provider=provider_name,
            model=resp.model or request.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=cost_usd,
        )
        resp.source_billing_type = getattr(source, "billing_type", "usage_based") if source is not None else "usage_based"
        # Past the chokepoint source is guaranteed non-None; local-vs-cloud comes
        # from the source's is_local flag, NOT billing_type (a flat_rate cloud
        # source is still cloud).
        resp.source_kind = "local" if getattr(source, "is_local", False) else "cloud"
        return resp

    def source_health(self) -> dict[str, bool]:
        """Return current health status of all registered sources."""
        return {s.name: s.available for s in self._sources.all()}

    def _or_call(self, req: InferenceRequest) -> dict:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        spec = self._models.get(req.model)
        apply_cache = spec is not None and spec.cacheable and bool(req.system)
        if req.system:
            if apply_cache:
                system_msg: dict = {
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
                system_msg = {"role": "system", "content": req.system}
            messages = [system_msg] + req.messages
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
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/UnseenUniversity",
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
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        tool_calls = msg.get("tool_calls") or None
        usage = raw.get("usage") or {}
        return InferenceResponse(
            text=text,
            model=raw.get("model", ""),
            finish_reason=finish_reason,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_estimate=float(usage.get("cost") or 0.0),
            elapsed_ms=elapsed_ms,
            raw=raw,
            tool_calls=tool_calls,
        )
    # Ollama /api/chat native format
    msg = raw.get("message") or {}
    text = msg.get("content") or ""
    # Ollama /api/chat returns native tool_calls on the message (same shape the ToolLoop
    # reads from the OpenAI-format branch above). Without extracting them here, a local
    # Ollama model's tool calls are invisible and it looks like plain prose.
    tool_calls = msg.get("tool_calls") or None
    done_reason = raw.get("done_reason") or ("stop" if raw.get("done") else "")
    return InferenceResponse(
        text=text,
        model=raw.get("model", ""),
        finish_reason=done_reason or "stop",
        input_tokens=raw.get("prompt_eval_count", 0),
        output_tokens=raw.get("eval_count", 0),
        elapsed_ms=elapsed_ms,
        tool_calls=tool_calls,
        raw=raw,
    )
