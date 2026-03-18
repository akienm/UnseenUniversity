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

# ── Data model ──────────────────────────────────────────────────────────────────


@dataclass
class InferenceContext:
    """
    Live routing-state snapshot. Constructed fresh before each gateway.call().
    Passed to every edge condition; handlers may read last_elapsed_ms.
    """

    cloud_active: bool  # is_cloud_training_active()
    local_available: bool  # Ollama health check passed
    balance_ok: bool  # OR api key present AND balance above floor
    is_background: bool  # impulse / background turn (no latency requirement)
    cloud_ok_override: bool = (
        True  # D071: False = night/local-only mode; gates background cloud calls
    )
    last_elapsed_ms: float = 0.0  # set by gateway after each handler attempt


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
        Raises RoutingError if no handler succeeds.
        """
        try:
            from .forensic_logger import log_pipeline_step as _lpt, get_turn_id as _gtid
        except Exception:
            _lpt = None
            _gtid = lambda: "?"

        constraints = self._purposes.get(
            purpose_id, PurposeConstraints(step_name=purpose_id)
        )
        current_id = purpose_id
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
                        except Exception:
                            pass
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

        # Tier 2: local Ollama pools
        try:
            from ..brainstem.local_pool import LocalKoboldPool, BatchKoboldPool

            gw._t2 = LocalKoboldPool()
            gw._t2_batch = BatchKoboldPool(fallback=gw._t2)
        except Exception as _e:
            _log.getLogger(__name__).warning(f"[gateway] local pool init failed: {_e}")

        # Tiers 3 / 3.5 / 4: OpenRouter
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                from .reasoners.openrouter_reasoner import OpenRouterReasoner

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
                _log.getLogger(__name__).warning(
                    f"[gateway] OpenRouter init failed: {_e}"
                )

        # Tier 5: Anthropic direct (inhibited by IGOR_TIER5_ENABLED)
        try:
            from .reasoners.anthropic import AnthropicReasoner

            gw._t5 = AnthropicReasoner()
        except Exception as _e:
            _log.getLogger(__name__).warning(f"[gateway] Anthropic init failed: {_e}")

        return gw

    # ── Primary reasoning interface ────────────────────────────────────────────

    def reason(
        self,
        user_input: str,
        relevant: list,
        core: list,
        *,
        level: str = "interactive",
        skip_to: str = "tier.3.5",
        preparse_csb: str = "",
        thread_id: Optional[str] = None,
        cortex=None,
        instance_id: str = "",
        local_only: bool = False,
        on_tier: Optional[Callable[[str], None]] = None,
    ) -> "tuple[str, float, bool]":
        """
        Route a reasoning request through the tier ladder. Single call site for
        all inference in Igor — no tier strings or backend names leak to callers.

        level:
          "background"       NE impulses; cloud_mode → tier.3, else tier.2/impulse
          "background_batch" PROACTIVE_HABIT; batch pool only (quality > speed)
          "interactive"      Human turns; cascade from skip_to upward

        skip_to: minimum starting tier for interactive turns ("tier.3"|"tier.3.5"|"tier.4").
                 Computed by caller from preparse complexity + milieu signals.
                 Gateway executes the cascade from that tier onward.

        on_tier: callback fired at each tier attempt — use for activity broadcast.
                 Signature: (tier_label: str) -> None

        Returns (response_text, cost_usd, used_api).
        """
        self.last_tier = ""
        _log_err = None
        try:
            from .forensic_logger import log_error as _log_err
        except Exception:
            pass

        if local_only:
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
                        self.last_tier = "tier.2/budget"
                        if on_tier:
                            on_tier("tier.2/budget")
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
                                kind="TIER_FAIL", source="tier.2/budget", detail=str(_e)
                            )
        except Exception:
            pass

        # ── Background: batch impulse (always local, quality priority) ─────────
        if level == "background_batch":
            pool = self._t2_batch or self._t2
            if pool:
                try:
                    self.last_tier = "tier.2/batch"
                    if on_tier:
                        on_tier("tier.2/batch")
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
                            kind="IMPULSE_SKIP", source="tier.2/batch", detail=str(_e)
                        )
            return "", 0.0, False

        # ── Background: impulse (cloud if active, else local) ─────────────────
        if level == "background":
            _cloud_active = False
            try:
                from .cloud_mode import is_cloud_training_active as _cma

                _cloud_active = _cma()
            except Exception:
                pass

            if _cloud_active and self._t3:
                try:
                    self.last_tier = "tier.3/impulse"
                    if on_tier:
                        on_tier("tier.3/impulse")
                    text, cost = self._t3.reason(
                        user_input,
                        relevant,
                        core,
                        instance_id,
                        cortex=cortex,
                        preparse_csb="",
                        thread_id=thread_id,
                    )
                    return text, cost, True
                except Exception as _e:
                    if _log_err:
                        _log_err(
                            kind="IMPULSE_CLOUD_FAIL",
                            source="tier.3/impulse",
                            detail=str(_e),
                        )
                    return "", 0.0, False

            if self._t2:
                try:
                    self.last_tier = "tier.2/impulse"
                    if on_tier:
                        on_tier("tier.2/impulse")
                    text, cost = self._t2.reason(
                        user_input, relevant, core, instance_id, force_local=True
                    )
                    return text, cost, False
                except Exception as _e:
                    if _log_err:
                        _log_err(
                            kind="IMPULSE_SKIP", source="tier.2/impulse", detail=str(_e)
                        )
            return "", 0.0, False

        # ── Interactive: tier cascade from skip_to upward ─────────────────────
        last_error = ""

        if skip_to == "tier.3" and self._t3:
            try:
                self.last_tier = "tier.3"
                if on_tier:
                    on_tier("tier.3")
                text, cost = self._t3.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    preparse_csb=preparse_csb,
                    thread_id=thread_id,
                )
                return text, cost, True
            except Exception as _e:
                last_error = str(_e)
                if _log_err:
                    _log_err(kind="TIER_FAIL", source="tier.3", detail=str(_e))

        if skip_to in ("tier.3", "tier.3.5") and self._t35:
            try:
                self.last_tier = "tier.3.5"
                if on_tier:
                    on_tier("tier.3.5")
                text, cost = self._t35.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    preparse_csb=preparse_csb,
                    thread_id=thread_id,
                )
                return text, cost, True
            except Exception as _e:
                last_error = str(_e)
                if _log_err:
                    _log_err(kind="TIER_FAIL", source="tier.3.5", detail=str(_e))

        if self._t4:
            try:
                self.last_tier = "tier.4"
                if on_tier:
                    on_tier("tier.4")
                text, cost = self._t4.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    preparse_csb=preparse_csb,
                    thread_id=thread_id,
                )
                return text, cost, True
            except Exception as _e:
                last_error = str(_e)
                if _log_err:
                    _log_err(kind="TIER_FAIL", source="tier.4", detail=str(_e))

        if (
            os.getenv("IGOR_TIER5_ENABLED", "false").lower() in ("1", "true", "yes")
            and self._t5
        ):
            try:
                self.last_tier = "tier.5"
                if on_tier:
                    on_tier("tier.5")
                text, cost = self._t5.reason(
                    user_input,
                    relevant,
                    core,
                    instance_id,
                    cortex=cortex,
                    preparse_csb=preparse_csb,
                    thread_id=thread_id,
                )
                return text, cost, True
            except Exception as _e:
                last_error = str(_e)
                if _log_err:
                    _log_err(kind="TIER_FAIL", source="tier.5", detail=str(_e))
        else:
            last_error = last_error or "tier.5 inhibited (IGOR_TIER5_ENABLED not set)"

        # ── tier.6: all inference exhausted ───────────────────────────────────
        self.last_tier = "tier.6"
        try:
            from .forensic_logger import log_anomaly as _log_anomaly

            _log_anomaly(kind="TIER6", detail=f"last_error={last_error[:160]}")
        except Exception:
            pass
        try:
            from ..arbiter import queue as _arb

            _arb.submit(
                description="All cloud inference failed — Igor offline",
                context=f"Last error: {last_error[:200]}",
                action_type="system_alert",
                threshold_reason="Total cloud inference failure (tiers 3-5 all failed)",
                metadata={"tier_failures": ["tier.3", "tier.4", "tier.5"]},
            )
        except Exception:
            pass
        return (
            "⚠ All cloud inference is currently unavailable. "
            "I've queued a notification for akien.",
            0.0,
            False,
        )


# ── Handler callables ────────────────────────────────────────────────────────────
# Accept (prompt, constraints, **kwargs) → str.
# Raise on any failure. Return non-empty string on success.
# Model + host read from constraints.extra so purpose drives configuration.


def _h_ollama(prompt: str, c: PurposeConstraints, **kw) -> str:
    """Raw Ollama /api/chat. Raises on any error or blank response."""
    model = (
        kw.get("model")
        or c.extra.get("model")
        or os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
    )
    host = c.extra.get("host") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
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
    """Ollama available AND cloud training mode not active."""
    return ctx.local_available and not ctx.cloud_active


def _cloud_preferred(ctx: InferenceContext) -> bool:
    """Cloud training active OR local unavailable. Blocked for background if night mode (D071)."""
    if ctx.is_background and not ctx.cloud_ok_override:
        return False
    return ctx.cloud_active or not ctx.local_available


def _cloud_ok(ctx: InferenceContext) -> bool:
    """OR balance above floor and API key present. Blocked for background if night mode (D071)."""
    if ctx.is_background and not ctx.cloud_ok_override:
        return False
    return ctx.balance_ok


def _ne_local_ok(ctx: InferenceContext) -> bool:
    """NE local model env var set, Ollama available, cloud_mode not active."""
    return (
        bool(os.getenv("IGOR_NE_LOCAL_MODEL", ""))
        and ctx.local_available
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
                    "model": os.getenv(
                        "OLLAMA_REASONING_MODEL",
                        os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
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
                    "model": os.getenv("IGOR_WINNOW_LOCAL_MODEL", "llama3.2:1b"),
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
                    "model": os.getenv("IGOR_NE_LOCAL_MODEL", ""),
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
                    "model": os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
                },
            ),
        ),
    ]
    for node_id, constraints in purposes:
        gw.add_node(Node(id=node_id))
        gw.register_purpose(node_id, constraints)

    # ── Handler nodes (one Ollama + one OR per purpose, except think) ─────────
    for node_id in ("ollama_preparse", "ollama_winnow", "ollama_ne", "ollama_think"):
        gw.add_node(Node(id=node_id, handler=_h_ollama))
    for node_id in ("or_preparse", "or_winnow", "or_ne"):
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

    return gw


# ── Context factory ───────────────────────────────────────────────────────────────


def make_context(is_background: bool = False) -> InferenceContext:
    """Build a fresh InferenceContext by checking live system state."""
    cloud_active = False
    try:
        from .cloud_mode import is_cloud_training_active

        cloud_active = is_cloud_training_active()
    except Exception:
        pass

    cloud_ok_override = True
    try:
        from .cloud_mode import is_cloud_ok_override as _cko

        cloud_ok_override = _cko()
    except Exception:
        pass

    local_available = False
    try:
        from .reasoners.ollama_reasoner import is_healthy as _ollama_healthy

        local_available = _ollama_healthy()
    except Exception:
        pass

    balance_ok = False
    try:
        if os.getenv("OPENROUTER_API_KEY", ""):
            from ..tools.budget import budget_status

            balance_ok = budget_status().get("remaining_usd", 1.0) > 0.50
    except Exception:
        balance_ok = bool(os.getenv("OPENROUTER_API_KEY", ""))

    return InferenceContext(
        cloud_active=cloud_active,
        local_available=local_available,
        balance_ok=balance_ok,
        is_background=is_background,
        cloud_ok_override=cloud_ok_override,
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
    except Exception:
        pass

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
        from .reasoners.ollama_reasoner import is_healthy as _h

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
        from .reasoners.ollama_reasoner import is_healthy as _h

        if _h():
            return True
        return _try_restart_local_ollama()
    except Exception:
        return False
