"""
inference_gateway.py — Unified inference routing as a directed acyclic graph (DAG).

All low-cost single-shot inference calls (preparse, winnow, NE, think) go through here.
Routing policy lives entirely in this file as a small DAG:

  - Purpose nodes  — entry points; carry call constraints (max_tokens, timeout, model)
  - Handler nodes  — leaves; wrap a specific model/endpoint; raise on failure
  - Edges          — directed, condition-gated, priority-ordered;
                     fallback edges only fire after a handler raises

Traversal: purpose → (evaluate edge conditions in priority order) → handler.
On handler failure: follow fallback edges. Raise RoutingError if no path succeeds.

Changing routing policy means editing build_default_gateway() — not hunting
through 5 files. Visibility: gateway.describe() or /routing --dag.

Pass 1 (session 2026-03-12o):
  preparse  — Ollama reasoning model → OR cheap fallback
  winnow    — Ollama local → OR cheap fallback
  ne        — Ollama NE model ↔ OR cheap (cloud_mode inverts preference)
  think     — Ollama local only (no cloud fallback)

Pass 2 (future): interactive _reason_with_failover() — complex, tool-using turns.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

# ── Data model ──────────────────────────────────────────────────────────────────


@dataclass
class InferenceContext:
    """
    Live routing-state snapshot. Constructed fresh before each gateway.call().
    Passed to every edge condition; handlers may read last_elapsed_ms.

    D211 routing: local-first. Cloud only when:
      - is_user_turn AND complexity in {medium, high}  (quality/voice matters)
      - research_mode AND cloud_ok_override            (research quality)
      - no local capacity                              (fallback)
    """

    cloud_active: bool  # is_cloud_training_active() — time-of-day + intent
    local_available: bool  # Ollama health check passed
    balance_ok: bool  # OR api key present AND balance above floor
    is_background: bool  # impulse / background turn (no latency requirement)
    cloud_ok_override: bool = True  # D071: False = night/local-only; gates background
    last_elapsed_ms: float = 0.0  # set by gateway after each handler attempt
    db_colocated: bool = False  # D205: Postgres on same host as Ollama
    # D211: routing intent signals
    is_user_turn: bool = False  # this call is part of a reply to a human
    research_mode: bool = False  # call chain is research (book reader, web extract)
    complexity: str = "low"  # low | medium | high — from thalamus parsed_input


@dataclass
class PurposeConstraints:
    """
    Call constraints attached to a purpose node.
    Travel unchanged through traversal; every handler receives them.
    """

    step_name: str  # pipeline_trace step label
    max_tokens: int = 256
    timeout_s: float = 8.0
    temperature: float = 0.1
    extra: dict = field(default_factory=dict)  # purpose-specific overrides


@dataclass
class Node:
    id: str
    handler: Optional[Callable] = None  # None → routing node; callable → leaf handler

    @property
    def is_handler(self) -> bool:
        return self.handler is not None


@dataclass
class Edge:
    source: str
    target: str
    condition: Callable[[InferenceContext], bool]
    priority: int = 0
    is_fallback: bool = False
    label: str = ""  # human-readable label for describe()


class RoutingError(RuntimeError):
    pass


# ── Gateway ──────────────────────────────────────────────────────────────────────


class InferenceGateway(IgorBase):
    def __init__(self) -> None:
        super().__init__()
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, list[Edge]] = {}
        self._purposes: dict[str, PurposeConstraints] = {}
        # Tier reasoner instances — populated by from_env()
        self._t2 = None  # tier.2       local Ollama (interactive timeout)
        self._t2_batch = None  # tier.2 batch local Ollama (quality priority)
        self._t3 = None  # tier.3       OR cheap (gpt-4o-mini)
        self._t35 = None  # tier.3.5     OR haiku  (persona-capable)
        self._t4 = None  # tier.4       OR sonnet
        self._t5 = None  # tier.5       Anthropic direct (inhibited)
        self.last_tier: str = ""  # set after every reason() call

    # ── Registration ──────────────────────────────────────────────────────────

    def add_node(self, node: Node) -> None:
        self._nodes[node.id] = node
        self._edges.setdefault(node.id, [])

    def add_edge(self, edge: Edge) -> None:
        self._edges.setdefault(edge.source, [])
        self._edges[edge.source].append(edge)

    def register_purpose(self, node_id: str, constraints: PurposeConstraints) -> None:
        self._purposes[node_id] = constraints

    # ── Traversal ─────────────────────────────────────────────────────────────

    def call(
        self,
        purpose_id: str,
        prompt: str,
        ctx: InferenceContext,
        **kwargs,
    ) -> str:
        """
        Traverse from purpose_id to a handler node, return response text.
        kwargs are forwarded to every handler attempt.

        Special kwargs (consumed here, not forwarded to handlers):
          handler_override: str — jump directly to a named handler node,
            skipping DAG edge traversal. Logging and fallback still fire.
            Used by benchmarking to force ollama vs OR endpoint.
        Raises RoutingError if no handler succeeds.
        """
        try:
            from .forensic_logger import log_pipeline_step as _lpt, get_turn_id as _gtid
        except Exception:
            _lpt = None
            _gtid = lambda: "?"

        # Pop handler_override so it doesn't forward to handlers
        handler_override = kwargs.pop("handler_override", None)

        constraints = self._purposes.get(
            purpose_id, PurposeConstraints(step_name=purpose_id)
        )
        current_id = handler_override if handler_override else purpose_id
        failed: set[str] = set()

        while True:
            node = self._nodes.get(current_id)
            if node is None:
                raise RoutingError(f"InferenceGateway: unknown node '{current_id}'")

            if node.is_handler:
                t0 = time.monotonic()
                try:
                    result = node.handler(prompt, constraints, **kwargs)
                    ms = round((time.monotonic() - t0) * 1000)
                    ctx.last_elapsed_ms = float(ms)
                    if _lpt:
                        try:
                            _lpt(
                                turn_id=_gtid(),
                                step=constraints.step_name,
                                elapsed_ms=ms,
                                via=current_id,
                            )
                        except Exception as _bare_e:
                            log_error(
                                kind="BARE_EXCEPT",
                                detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
                            )
                    return result
                except Exception as exc:
                    ctx.last_elapsed_ms = round((time.monotonic() - t0) * 1000)
                    failed.add(current_id)
                    fallbacks = sorted(
                        [
                            e
                            for e in self._edges.get(current_id, [])
                            if e.is_fallback
                            and e.target not in failed
                            and e.condition(ctx)
                        ],
                        key=lambda e: e.priority,
                    )
                    if not fallbacks:
                        raise RoutingError(
                            f"Handler '{current_id}' failed, no fallback available: {exc}"
                        ) from exc
                    current_id = fallbacks[0].target
                    continue

            # Routing node — walk highest-priority passing non-fallback edge
            candidates = sorted(
                [
                    e
                    for e in self._edges.get(current_id, [])
                    if not e.is_fallback and e.target not in failed and e.condition(ctx)
                ],
                key=lambda e: e.priority,
            )
            if not candidates:
                raise RoutingError(
                    f"InferenceGateway: no passing edges from routing node '{current_id}'"
                )
            current_id = candidates[0].target

    # ── Visibility ────────────────────────────────────────────────────────────

    def describe(self) -> str:
        """Human-readable DAG — used by /routing --dag."""
        lines = ["── Inference Gateway — routing DAG ──", ""]
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            role = "handler" if node.is_handler else "router "
            c = self._purposes.get(node_id)
            c_str = (
                f"  [max_tokens={c.max_tokens} timeout={c.timeout_s}s temp={c.temperature}]"
                if c
                else ""
            )
            lines.append(f"  [{role}] {node_id}{c_str}")
            for e in sorted(self._edges.get(node_id, []), key=lambda e: e.priority):
                fb = " [fallback]" if e.is_fallback else ""
                lbl = f" ({e.label})" if e.label else ""
                lines.append(f"    ──[pri={e.priority}{fb}]──▶  {e.target}{lbl}")
        return "\n".join(lines)

    # ── Reasoner factory ──────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "InferenceGateway":
        """
        Build the default routing DAG and instantiate all tier reasoners from env.
        This is the single place that knows about Ollama, OpenRouter, and Anthropic.
        Called once at Igor boot; result stored on Igor as self._gateway.
        """
        import logging as _log

        gw = build_default_gateway()

        # Tier 2: local Ollama (interactive fallback + background impulse)
        try:
            from .inference_ollama import OllamaReasoner as _OR

            gw._t2 = _OR(
                model=os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
                host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            )
            _log.getLogger(__name__).info(
                f"[gateway] Ollama tier.2 ready — model={gw._t2.model}"
            )
        except Exception as _e:
            _log.getLogger(__name__).warning(
                f"[gateway] Ollama tier.2 init failed: {_e}"
            )

        # Tiers 3 / 3.5 / 4: OpenRouter
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                from .inference_openrouter import OpenRouterReasoner

                gw._t3 = OpenRouterReasoner(
                    model=os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
                )
                gw._t35 = OpenRouterReasoner(
                    model=os.getenv(
                        "OPENROUTER_DEFAULT_MODEL",
                        os.getenv(
                            "OPENROUTER_INTERACTIVE_MODEL", "anthropic/claude-haiku-4.5"
                        ),
                    )
                )
                gw._t4 = OpenRouterReasoner(
                    model=os.getenv(
                        "OPENROUTER_INTERACTIVE_MODEL", "anthropic/claude-sonnet-4.6"
                    )
                )
                _log.getLogger(__name__).info(
                    f"[gateway] OpenRouter ready — t3={gw._t3.model} t35={gw._t35.model} t4={gw._t4.model}"
                )
            except Exception as _e:
                _log.getLogger(__name__).error(
                    f"[gateway] OpenRouter init FAILED — cloud will be unavailable: {type(_e).__name__}: {_e}"
                )
        else:
            _log.getLogger(__name__).debug(
                "[gateway] OpenRouter API key not configured — cloud inference disabled"
            )

        # Tier 5: Anthropic direct — REMOVED (D329: OR handles all cloud routing)
        gw._t5 = None

        return gw

    # ── Primary reasoning interface ────────────────────────────────────────────

    def reason(
        self,
        user_input: str,
        relevant: list,
        core: list,
        *,
        level: str = "interactive",
        skip_to: str = "tier.3.5",  # deprecated — ignored; kept for caller compat (D198)
        preparse_csb: str = "",
        thread_id: Optional[str] = None,
        cortex=None,
        instance_id: str = "",
        local_only: bool = False,
        on_tier: Optional[Callable[[str], None]] = None,
        is_user_turn: bool = False,  # D211: human web turn
        complexity: str = "low",  # D211: low|medium|high from thalamus
    ) -> "tuple[str, float, bool]":
        """
        Route a reasoning request. Three call profiles, binary cloud/local decision. (D198)

        level:
          "interactive"      Human turns:         cloud=sonnet | local=fastest-box
          "background"       NE impulses:         cloud=gpt-4o-mini | local=fastest-box
          "background_batch" Proactive habits:    always local, quality priority

        skip_to: DEPRECATED — ignored. Kept for caller compatibility.

        on_tier: callback fired at each attempt — used for activity broadcast.
                 Signature: (label: str) -> None

        Returns (response_text, cost_usd, used_api).
        """
        self.last_tier = ""
        _log_err = log_error  # forensic hook — wired to log_error for TIER_FAIL entries

        # ── local_only: caller explicitly wants local (cloud_ok_override=False) ─
        if local_only:
            if self._t2:
                try:
                    self.last_tier = "local/forced"
                    if on_tier:
                        on_tier("local/forced")
                    text, cost = self._t2.reason(
                        user_input,
                        relevant,
                        core,
                        instance_id,
                        cortex=cortex,
                        thread_id=thread_id,
                        interactive_fallback=True,
                    )
                    return text, cost, False
                except Exception as _e:
                    if _log_err:
                        _log_err(
                            kind="TIER_FAIL", source="local/forced", detail=str(_e)
                        )
            return (
                "I'm operating in local-only mode, but my local model is unavailable "
                "right now. Please try a simpler task or remove the 'local only' constraint.",
                0.0,
                False,
            )

        # ── Budget depletion guard ─────────────────────────────────────────────
        try:
            from ..tools.budget import is_cloud_blocked as _blocked_check

            _blocked, _block_reason = _blocked_check()
            if _blocked:
                if _log_err:
                    _log_err(
                        kind="BUDGET_BLOCK",
                        source="gateway.reason",
                        detail=_block_reason,
                    )
                if self._t2:
                    try:
                        self.last_tier = "local/budget"
                        if on_tier:
                            on_tier("local/budget")
                        text, cost = self._t2.reason(
                            user_input,
                            relevant,
                            core,
                            instance_id,
                            cortex=cortex,
                            thread_id=thread_id,
                        )
                        return text, cost, False
                    except Exception as _e:
                        if _log_err:
                            _log_err(
                                kind="TIER_FAIL", source="local/budget", detail=str(_e)
                            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
            )

        # ── Cloud availability: single check, shared across all profiles ───────
        # _t4 is only initialized when OPENROUTER_API_KEY is present (from_env).
        # is_cloud_training_active() is a research-mode flag — NOT the availability gate.
        # If we have a live t4 reasoner, cloud is available. (D198 fix)
        _cloud_ok = bool(self._t4)
        _cloud_attempted = False  # Track if we even tried cloud
        _cloud_error = ""  # Track why cloud failed, if at all
        if not _cloud_ok:
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "[inference_gateway] cloud unavailable: _t4=%s (check OPENROUTER_API_KEY init at boot)",
                "initialized" if self._t4 else "None",
            )

        # ── background_batch: always local, quality priority ──────────────────
        if level == "background_batch":
            pool = self._t2_batch or self._t2
            if pool:
                try:
                    self.last_tier = "local/batch"
                    if on_tier:
                        on_tier("local/batch")
                    if hasattr(pool, "reason_batch"):
                        text, cost = pool.reason_batch(
                            user_input, relevant, core, instance_id
                        )
                    else:
                        text, cost = pool.reason(
                            user_input, relevant, core, instance_id, force_local=True
                        )
                    return text, cost, False
                except Exception as _e:
                    if _log_err:
                        _log_err(
                            kind="IMPULSE_SKIP", source="local/batch", detail=str(_e)
                        )
            return "", 0.0, False

        # ── background: Ollama primary, drop if fails (D234: OR is scarce luxury) ──
        # Background impulses don't warrant OR budget — quality doesn't matter here.
        if level == "background":
            if self._t2:
                try:
                    self.last_tier = "local/background"
                    if on_tier:
                        on_tier("local/background")
                    text, cost = self._t2.reason(
                        user_input, relevant, core, instance_id, force_local=True
                    )
                    return text, cost, False
                except Exception as _e:
                    if _log_err:
                        _log_err(
                            kind="IMPULSE_SKIP",
                            source="local/background",
                            detail=str(_e),
                        )
            return "", 0.0, False

        # ── interactive: D254 human turns → cloud direct; D234 Ollama primary for background ──
        # D254: human turns (is_user_turn=True) skip Ollama entirely — go straight to cloud.
        # Ollama reserved for background/non-human interactive turns.
        # Cloud budget exhaustion is the only condition that falls back to Ollama for human turns.
        last_error = ""
        _quality_path = is_user_turn and complexity in ("medium", "high")

        # D268: MODE override — check TWM for active min_tier entry before tier selection.
        # Habit PROC_READING_BOOTSTRAP (and future mode habits) push:
        #   twm_push(content_csb="MODE|...|min_tier=tier.4", category="mode_override", ttl_seconds=N)
        # If active, force cloud path regardless of is_user_turn / complexity.
        _force_cloud_mode = False
        if cortex is not None:
            try:
                _mode_entries = cortex.twm_read(
                    limit=5, category="mode_override", include_integrated=True
                )
                for _me in reversed(_mode_entries):
                    _csb = _me.get("content_csb", "")
                    if "min_tier=tier.4" in _csb:
                        _force_cloud_mode = True
                        try:
                            from .forensic_logger import (
                                log_cognition_metric as _lcm_mode,
                            )

                            _lcm_mode(
                                metric="mode_override",
                                value=1.0,
                                detail=f"min_tier=tier.4 from TWM: {_csb[:80]}",
                            )
                        except Exception:
                            pass
                        break
            except Exception as _mode_e:
                log_error(kind="MODE_READ_FAIL", detail=str(_mode_e))

        if self._t2 and not is_user_turn and not _force_cloud_mode:
            try:
                self.last_tier = "local/interactive"
                if on_tier:
                    on_tier("local/interactive")
                text, cost = self._t2.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    thread_id=thread_id,
                    interactive_fallback=True,
                )
                return text, cost, False
            except Exception as _e:
                last_error = str(_e)
                _kind = (
                    "STALL"
                    if (
                        "timed out" in last_error.lower()
                        or "timeout" in last_error.lower()
                    )
                    else "DOWN"
                )
                if _log_err:
                    _log_err(
                        kind=f"TIER_FAIL_{_kind}",
                        source="local/interactive",
                        detail=str(_e),
                    )

        # Cloud path: always for human turns (D254); quality path for non-human (D234);
        # D268: forced when MODE override is active (e.g. reading bootstrap min_tier=tier.4)
        if (
            (is_user_turn or _quality_path or _force_cloud_mode)
            and _cloud_ok
            and self._t4
        ):
            _cloud_attempted = True
            try:
                self.last_tier = "cloud/interactive"
                if on_tier:
                    on_tier("cloud/interactive")
                text, cost = self._t4.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    preparse_csb=preparse_csb,
                    thread_id=thread_id,
                    no_tools=True,  # 161 tools → provider 400; interactive turns are conversational
                )
                return text, cost, True
            except Exception as _e:
                last_error = str(_e)
                _cloud_error = last_error  # Capture cloud-specific error
                if _log_err:
                    _log_err(
                        kind="TIER_FAIL", source="cloud/interactive", detail=str(_e)
                    )

        # ── last-resort local retry: cloud failed (or wasn't tried), retry Ollama ──
        # Cloud fail → try local once more before giving up entirely.
        # Skip retry if the first local failure was a timeout — it'll just stall again.
        # Only retry on connection errors (host was briefly unreachable, may have recovered).
        # D254: human turns always allowed to retry Ollama as budget-exhaustion fallback.
        _was_timeout = (
            "timed out" in last_error.lower() or "timeout" in last_error.lower()
        )
        if self._t2 and last_error and (is_user_turn or not _was_timeout):
            try:
                self.last_tier = "local/retry"
                if on_tier:
                    on_tier("local/retry")
                text, cost = self._t2.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    thread_id=thread_id,
                    interactive_fallback=True,
                )
                return text, cost, False
            except Exception as _e:
                if _log_err:
                    _log_err(kind="TIER_FAIL", source="local/retry", detail=str(_e))

        # ── total failure: cloud + local both failed ──────────────────────────
        # T-inference-monitor: proactive detection now lives in ResourceMonitorSource.
        # tier.6 only logs forensically — no inline arbiter submit needed.
        self.last_tier = "tier.6"

        # Build diagnostic breadcrumb: what was attempted, what failed?
        _diagnostic = []
        if _cloud_attempted:
            _diagnostic.append(
                f"cloud_attempted={_cloud_ok and self._t4} error={_cloud_error[:80]}"
            )
        else:
            _diagnostic.append(
                f"cloud_skipped=(is_user_turn={is_user_turn} OR quality_path={_quality_path} OR force_mode={_force_cloud_mode}) AND cloud_ok={_cloud_ok} AND t4={bool(self._t4)}"
            )
        if last_error:
            _diagnostic.append(f"local_error={last_error[:80]}")
        _diagnostic_str = " | ".join(_diagnostic)

        try:
            from .forensic_logger import log_anomaly as _log_anomaly

            try:
                import psutil as _psutil

                _cpu = _psutil.cpu_percent(interval=0.1)
                _mem = _psutil.virtual_memory()
                _resource_detail = (
                    f"cpu={_cpu:.0f}% mem_used={_mem.percent:.0f}% "
                    f"mem_avail_mb={_mem.available // 1024 // 1024}"
                )
            except Exception:
                _resource_detail = "resource_stats=unavailable"
            _log_anomaly(
                kind="TIER6",
                detail=f"both_inference_unavailable: {_diagnostic_str} | {_resource_detail}",
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
            )
        return (
            "⚠ Both cloud and local inference are unavailable. "
            "I'll let you know when inference is restored.",
            0.0,
            False,
        )


# ── Handler callables ────────────────────────────────────────────────────────────
# Accept (prompt, constraints, **kwargs) → str.
# Raise on any failure. Return non-empty string on success.
# Model + host read from constraints.extra so purpose drives configuration.


def _h_ollama(prompt: str, c: PurposeConstraints, **kw) -> str:
    """Raw Ollama /api/chat. Raises on any error or blank response.

    D120: if extra contains cluster_call_type, asks cluster_router for the best
    (host, model) at call time. Falls back to static extra values if router returns None.
    """
    call_type = c.extra.get("cluster_call_type", "")
    if call_type:
        try:
            from .inference_ollama import router as _router

            r_host, r_model = _router.route(call_type)
        except Exception:
            r_host, r_model = None, None
    else:
        r_host, r_model = None, None

    model = (
        kw.get("model")
        or r_model
        or c.extra.get("model")
        or os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
    )
    host = (
        r_host
        or c.extra.get("host")
        or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": c.temperature, "num_predict": c.max_tokens},
        }
    ).encode()
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=int(c.timeout_s)) as resp:
        data = json.loads(resp.read())
    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise RuntimeError("Ollama returned empty response")
    return text


def _h_or(prompt: str, c: PurposeConstraints, **kw) -> str:
    """OpenRouter chat completion. Raises on any error or blank response."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    model = (
        kw.get("model")
        or c.extra.get("or_model")
        or os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
    )
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": c.temperature,
        "max_tokens": c.max_tokens,
    }
    if "response_format" in c.extra:
        body["response_format"] = c.extra["response_format"]
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("OR returned empty response")
    return text


# ── Edge conditions (pure functions of InferenceContext) ─────────────────────────


def _always(ctx: InferenceContext) -> bool:
    return True


def _local_preferred(ctx: InferenceContext) -> bool:
    """
    D234: Ollama primary. True unless local is physically unavailable.
    Ollama fires before OR regardless of complexity — OR is scarce budget.
    Reasons to route directly to OR:
      - research_mode  (research quality, large context — model capability matters)
      - db_colocated   (RAM contention with Postgres on same host)
      - no local capacity (nothing available)
    """
    if ctx.db_colocated:
        return False
    from .inference_ollama import router as _router

    if not _router.has_local_capacity():
        return False
    # Research mode: model capability matters more than budget
    if ctx.research_mode:
        return False
    return True


def _cloud_preferred(ctx: InferenceContext) -> bool:
    """
    D234: OR path when local is physically unavailable or research quality required.
    Blocked for background if night mode (D071).
    """
    if ctx.is_background and not ctx.cloud_ok_override:
        return False
    return not _local_preferred(ctx)


def _cloud_ok(ctx: InferenceContext) -> bool:
    """OR balance above floor and API key present. Blocked for background if night mode (D071)."""
    if ctx.is_background and not ctx.cloud_ok_override:
        return False
    return ctx.balance_ok


def _ne_local_ok(ctx: InferenceContext) -> bool:
    """NE local model env var set, cluster has local capacity, cloud_mode not active. (D120)"""
    from .inference_ollama import router as _router

    return (
        bool(os.getenv("IGOR_NE_LOCAL_MODEL", ""))
        and _router.has_local_capacity("ne")
        and not ctx.cloud_active
    )


def _cloud_training(ctx: InferenceContext) -> bool:
    """Cloud training mode active AND OR available (NE training preference)."""
    return ctx.cloud_active and ctx.balance_ok


# ── Default gateway factory ───────────────────────────────────────────────────────


def build_default_gateway() -> InferenceGateway:
    """
    Wire the default routing DAG.

    preparse  local_preferred ──▶ ollama_preparse ──[fallback]──▶ or_preparse
              cloud_preferred ──▶ or_preparse

    winnow    local_preferred ──▶ ollama_winnow   ──[fallback]──▶ or_winnow
              cloud_preferred ──▶ or_winnow

    ne        cloud_training  ──▶ or_ne
              ne_local_ok     ──▶ ollama_ne        ──[fallback]──▶ or_ne

    think     always          ──▶ ollama_think     (no cloud fallback)

    reading_extract
              local_preferred ──▶ ollama_reading   ──[fallback]──▶ or_reading
              cloud_preferred ──▶ or_reading
    """
    gw = InferenceGateway()

    # ── Purpose nodes ────────────────────────────────────────────────────────
    purposes = [
        (
            "preparse",
            PurposeConstraints(
                step_name="preparse_search",
                max_tokens=120,
                timeout_s=5.0,
                temperature=0.1,
                extra={
                    # D120: host + model resolved dynamically via cluster_router at call time.
                    # Fallback values used only if router returns (None, None).
                    "cluster_call_type": "preparse",
                    "model": os.getenv(
                        "OLLAMA_REASONING_MODEL",
                        os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
                    ),
                    "host": os.getenv(
                        "OLLAMA_REASONING_HOST",
                        os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    ),
                    "or_model": os.getenv(
                        "OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"
                    ),
                },
            ),
        ),
        (
            "winnow",
            PurposeConstraints(
                step_name="winnow",
                max_tokens=60,
                timeout_s=3.0,
                temperature=0.1,
                extra={
                    "cluster_call_type": "winnow",
                    "model": os.getenv("IGOR_WINNOW_LOCAL_MODEL", "llama3.2:1b"),
                    "host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    "or_model": os.getenv(
                        "OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"
                    ),
                },
            ),
        ),
        (
            "ne",
            PurposeConstraints(
                step_name="ne",
                max_tokens=1024,
                timeout_s=45.0,
                temperature=0.3,
                extra={
                    "cluster_call_type": "ne",
                    "model": os.getenv("IGOR_NE_LOCAL_MODEL", ""),
                    "host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    "or_model": os.getenv(
                        "OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"
                    ),
                    "response_format": {"type": "json_object"},
                },
            ),
        ),
        (
            "think",
            PurposeConstraints(
                step_name="think_llm",
                max_tokens=80,
                timeout_s=8.0,
                temperature=0.2,
                extra={
                    "cluster_call_type": "extraction",
                    "model": os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
                    "host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                },
            ),
        ),
        (
            "reading_extract",
            PurposeConstraints(
                step_name="reading_extract",
                max_tokens=220,
                timeout_s=300.0,  # background work — no short timeout; model swaps need time
                temperature=0.1,
                extra={
                    "cluster_call_type": "extraction",
                    "model": os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
                    "host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    "or_model": os.getenv(
                        "OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"
                    ),
                },
            ),
        ),
    ]
    for node_id, constraints in purposes:
        gw.add_node(Node(id=node_id))
        gw.register_purpose(node_id, constraints)

    # ── Handler nodes (one Ollama + one OR per purpose, except think) ─────────
    for node_id in (
        "ollama_preparse",
        "ollama_winnow",
        "ollama_ne",
        "ollama_think",
        "ollama_reading",
    ):
        gw.add_node(Node(id=node_id, handler=_h_ollama))
    for node_id in ("or_preparse", "or_winnow", "or_ne", "or_reading"):
        gw.add_node(Node(id=node_id, handler=_h_or))

    # ── Edges: preparse ──────────────────────────────────────────────────────
    gw.add_edge(
        Edge(
            "preparse",
            "ollama_preparse",
            _local_preferred,
            priority=1,
            label="local available, cloud_mode off",
        )
    )
    gw.add_edge(
        Edge(
            "preparse",
            "or_preparse",
            _cloud_preferred,
            priority=2,
            label="cloud_mode active or local unavailable",
        )
    )
    gw.add_edge(
        Edge(
            "ollama_preparse",
            "or_preparse",
            _cloud_ok,
            priority=1,
            is_fallback=True,
            label="ollama failed",
        )
    )

    # ── Edges: winnow ────────────────────────────────────────────────────────
    gw.add_edge(
        Edge(
            "winnow",
            "ollama_winnow",
            _local_preferred,
            priority=1,
            label="local available, cloud_mode off",
        )
    )
    gw.add_edge(
        Edge(
            "winnow",
            "or_winnow",
            _cloud_preferred,
            priority=2,
            label="cloud_mode active or local unavailable",
        )
    )
    gw.add_edge(
        Edge(
            "ollama_winnow",
            "or_winnow",
            _cloud_ok,
            priority=1,
            is_fallback=True,
            label="ollama failed",
        )
    )

    # ── Edges: ne ────────────────────────────────────────────────────────────
    gw.add_edge(
        Edge(
            "ne",
            "or_ne",
            _cloud_training,
            priority=1,
            label="cloud_mode active (training — prefers cloud)",
        )
    )
    gw.add_edge(
        Edge(
            "ne",
            "ollama_ne",
            _ne_local_ok,
            priority=2,
            label="local NE model set, cloud_mode off",
        )
    )
    gw.add_edge(
        Edge(
            "ollama_ne",
            "or_ne",
            _cloud_ok,
            priority=1,
            is_fallback=True,
            label="ollama_ne failed",
        )
    )
    # Unconditional fallback: no Ollama + cloud_mode off (e.g. Windows, nighttime)
    gw.add_edge(
        Edge(
            "ne",
            "or_ne",
            _always,
            priority=10,
            label="no local NE and cloud_mode off — OR fallback",
        )
    )

    # ── Edges: think ─────────────────────────────────────────────────────────
    gw.add_edge(
        Edge(
            "think",
            "ollama_think",
            _always,
            priority=1,
            label="always local (think never hits cloud)",
        )
    )
    # No fallback — _think_call() treats empty return as "no synthesis available"

    # ── Edges: reading_extract (D359) ────────────────────────────────────────
    gw.add_edge(
        Edge(
            "reading_extract",
            "ollama_reading",
            _local_preferred,
            priority=1,
            label="local available, cloud_mode off",
        )
    )
    gw.add_edge(
        Edge(
            "reading_extract",
            "or_reading",
            _cloud_preferred,
            priority=2,
            label="cloud_mode active or local unavailable",
        )
    )
    gw.add_edge(
        Edge(
            "ollama_reading",
            "or_reading",
            _cloud_ok,
            priority=1,
            is_fallback=True,
            label="ollama_reading failed",
        )
    )

    return gw


# ── Context factory ───────────────────────────────────────────────────────────────


def make_context(
    is_background: bool = False,
    is_user_turn: bool = False,
    research_mode: bool = False,
    complexity: str = "low",
) -> InferenceContext:
    """Build a fresh InferenceContext by checking live system state.

    D211: callers pass is_user_turn, research_mode, complexity to drive routing.
    main.py sets is_user_turn=True for human web turns.
    Pipeline calls set research_mode=True for book/web research chains.
    """
    cloud_active = False
    try:
        from .cloud_mode import is_cloud_training_active

        cloud_active = is_cloud_training_active()
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
        )

    cloud_ok_override = True
    try:
        from .cloud_mode import is_cloud_ok_override as _cko

        cloud_ok_override = _cko()
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
        )

    local_available = False
    try:
        from .inference_ollama import is_healthy as _ollama_healthy

        local_available = _ollama_healthy()
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
        )

    balance_ok = False
    try:
        if os.getenv("OPENROUTER_API_KEY", ""):
            from ..tools.budget import budget_status

            balance_ok = budget_status().get("remaining_usd", 1.0) > 0.50
    except Exception:
        balance_ok = bool(os.getenv("OPENROUTER_API_KEY", ""))

    # T-inference-colocation-signal: is Postgres on same host as Ollama?
    # When true, local inference competes with DB for RAM → prefer cloud.
    db_url = os.getenv("IGOR_HOME_DB_URL", "")
    db_colocated = (
        "localhost" in db_url
        or "127.0.0.1" in db_url
        or not db_url  # empty = SQLite path = always same box
    )

    return InferenceContext(
        cloud_active=cloud_active,
        local_available=local_available,
        balance_ok=balance_ok,
        is_background=is_background,
        cloud_ok_override=cloud_ok_override,
        db_colocated=db_colocated,
        is_user_turn=is_user_turn,
        research_mode=research_mode,
        complexity=complexity,
    )


# ── Module-level singleton ────────────────────────────────────────────────────────

_gateway: Optional[InferenceGateway] = None


def get_gateway() -> InferenceGateway:
    """Lazy singleton. First call builds the default gateway from env vars."""
    global _gateway
    if _gateway is None:
        _gateway = build_default_gateway()
    return _gateway


_OLLAMA_RESTART_LAST: float = 0.0  # epoch seconds of last restart attempt
_OLLAMA_RESTART_COOLDOWN: float = 60.0  # minimum seconds between attempts


def _try_restart_local_ollama() -> bool:
    """
    Attempt sudo systemctl restart ollama.service on the local machine.
    60-second cooldown prevents restart loops. Always-on (no env var gate).
    Returns True if Ollama is healthy after the attempt.
    """
    global _OLLAMA_RESTART_LAST
    now = time.time()
    if now - _OLLAMA_RESTART_LAST < _OLLAMA_RESTART_COOLDOWN:
        return False
    _OLLAMA_RESTART_LAST = now

    _log_anomaly = None
    try:
        from .forensic_logger import log_anomaly as _log_anomaly
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/inference_gateway.py: {_bare_e}",
        )

    try:
        import subprocess

        result = subprocess.run(
            ["sudo", "systemctl", "restart", "ollama.service"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            if _log_anomaly:
                _log_anomaly(
                    "OLLAMA_RESTART_FAIL",
                    f"exit={result.returncode} stderr={result.stderr.strip()[:100]}",
                )
            return False
        time.sleep(5)
        from .inference_ollama import is_healthy as _h

        healthy = _h()
        if _log_anomaly:
            _log_anomaly(
                "OLLAMA_RESTART_OK" if healthy else "OLLAMA_RESTART_UNHEALTHY",
                f"healthy={healthy}",
            )
        return healthy
    except Exception as exc:
        if _log_anomaly:
            _log_anomaly("OLLAMA_RESTART_ERROR", str(exc)[:100])
        return False


def is_local_inference_available() -> bool:
    """
    Ask the gateway whether local inference (Ollama) is online.
    If unhealthy, attempts automatic restart (60-second cooldown).
    This is the only correct place to ask — callers must not import
    ollama_reasoner.is_healthy() directly.
    """
    try:
        from .inference_ollama import is_healthy as _h

        if _h():
            return True
        return _try_restart_local_ollama()
    except Exception:
        return False


# ── D330: TWM-view tiered context builder ─────────────────────────────────────


def build_twm_context(
    cortex,
    tier: str = "tier.2",
    thread_id: str | None = None,
    relevant_memories: list | None = None,
) -> str:
    """
    D330: Build LLM context as a TWM simulation.

    The LLM sees what Igor sees — not a dump of everything, but a tiered
    view that mirrors Igor's current attention state.

    Tiers:
      tier.1       — not called (habit dispatch, no LLM)
      tier.2       — TWM snapshot only (minimal, fast)
      tier.3/3.5   — TWM snapshot + relevant memories
      tier.4       — TWM + memories + ring context + thread arc (full view)

    All output is prose (D330 finding: prose saves 7% tokens + better quality).
    Returns a context string to prepend/append to user input.
    """
    sections = []

    # ── Layer 1: TWM snapshot (always included) ──────────────────────────
    twm_block = _render_twm_snapshot(cortex, thread_id)
    if twm_block:
        sections.append(twm_block)

    # ── Layer 2: Relevant memories (tier.3+) ─────────────────────────────
    if tier not in ("tier.1", "tier.2") and relevant_memories:
        mem_block = _render_memories_prose(relevant_memories)
        if mem_block:
            sections.append(mem_block)

    # ── Layer 3: Ring context + thread arc (tier.4 only) ─────────────────
    if tier in ("tier.4", "tier.5"):
        ring_block = _render_ring_context(cortex, thread_id)
        if ring_block:
            sections.append(ring_block)

    return "\n\n".join(sections)


def _render_twm_snapshot(cortex, thread_id: str | None = None) -> str:
    """Render current TWM observations as prose."""
    try:
        twm_obs = cortex.twm_read(
            limit=10, include_integrated=False, thread_id=thread_id
        )
        if not twm_obs:
            return ""

        lines = ["Current attention (what I'm focused on right now):"]

        # Task sets first (active goals)
        tasks = [o for o in twm_obs if o.get("category") == "task_set"]
        for t in tasks:
            goal = t["content_csb"].replace("TASK_SET|", "").strip()
            lines.append(f"- ACTIVE TASK: {goal[:200]}")

        # Urgent observations
        urgent = [
            o
            for o in twm_obs
            if o.get("urgency", 0) >= 0.7
            and o.get("category") != "task_set"
            and o.get("source") not in ("narrative_engine", "ne_loop_guard")
        ]
        for o in sorted(urgent, key=lambda x: x.get("urgency", 0), reverse=True)[:3]:
            content = o["content_csb"].replace("|", " — ")
            lines.append(
                f"- URGENT (urgency {o.get('urgency', 0):.1f}): {content[:150]}"
            )

        # Regular observations by salience
        regular = [
            o
            for o in twm_obs
            if o.get("category") != "task_set" and o.get("urgency", 0) < 0.7
        ]
        for o in sorted(
            regular,
            key=lambda x: float(x.get("salience", 0))
            * (1 + float(x.get("attractor_weight", 0))),
            reverse=True,
        )[:5]:
            sal = float(o.get("salience", 0))
            content = o["content_csb"].replace("|", " — ")
            lines.append(f"- {content[:150]} (salience: {sal:.1f})")

        return "\n".join(lines)
    except Exception:
        return ""


def _render_memories_prose(memories: list) -> str:
    """Render relevant memories as prose (D330: prose > CSB for LLMs)."""
    if not memories:
        return ""
    lines = ["Relevant memories:"]
    for m in memories[:8]:
        narrative = m.narrative if hasattr(m, "narrative") else str(m)
        mem_type = m.memory_type.value if hasattr(m, "memory_type") else "unknown"
        mem_id = m.id if hasattr(m, "id") else ""
        lines.append(f"- [{mem_type}] {mem_id}: {narrative[:200]}")
    return "\n".join(lines)


def _render_ring_context(cortex, thread_id: str | None = None) -> str:
    """Render recent ring memory as prose for tier.4 full context."""
    try:
        from .reasoners.base import (
            _RING_EXCLUDE,
            _RING_CONTEXT_LIMIT,
            _RING_CONTEXT_MAX_AGE_HOURS,
        )
        from datetime import datetime, date

        all_entries = cortex.read_ring_memory(limit=50, thread_id=thread_id)

        # Age filter
        cutoff = datetime.now().timestamp() - _RING_CONTEXT_MAX_AGE_HOURS * 3600
        entries = [
            e
            for e in all_entries
            if e["category"] not in _RING_EXCLUDE
            and datetime.fromisoformat(e["timestamp"]).timestamp() >= cutoff
        ][-_RING_CONTEXT_LIMIT:]

        if not entries:
            return ""

        today = date.today().isoformat()

        def _ts(raw: str) -> str:
            if len(raw) < 10:
                return raw
            return raw[11:16] if raw[:10] == today else raw[:16]

        # Check for NE narrative anchor (last 10 min)
        narratives = cortex.read_ring_memory(
            limit=5, category="narrative", thread_id=thread_id
        )
        anchor = None
        if narratives:
            latest = narratives[-1]
            age = (
                datetime.now() - datetime.fromisoformat(latest["timestamp"])
            ).total_seconds()
            if age <= 600:
                arc = latest["content"]
                if arc.startswith("[NE#"):
                    arc = arc[arc.find("] ") + 2 :] if "] " in arc else arc
                anchor = arc[:240]

        lines = []
        if anchor:
            lines.append(f"Thread arc: {anchor}")
            # Only show entries after the anchor
            delta = [
                e for e in entries if e["timestamp"] > narratives[-1]["timestamp"]
            ][-5:]
            if delta:
                lines.append("Recent context (since last arc):")
                for e in delta:
                    lines.append(f"  [{_ts(e['timestamp'])}] {e['content']}")
        else:
            lines.append("Recent session context (newest last):")
            for e in entries:
                lines.append(f"  [{_ts(e['timestamp'])}] {e['content']}")

        return "\n".join(lines)
    except Exception:
        return ""
