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


# ── Data model ──────────────────────────────────────────────────────────────────

@dataclass
class InferenceContext:
    """
    Live routing-state snapshot. Constructed fresh before each gateway.call().
    Passed to every edge condition; handlers may read last_elapsed_ms.
    """
    cloud_active:    bool           # is_cloud_training_active()
    local_available: bool           # Ollama health check passed
    balance_ok:      bool           # OR api key present AND balance above floor
    is_background:   bool           # impulse / background turn (no latency requirement)
    last_elapsed_ms: float = 0.0    # set by gateway after each handler attempt


@dataclass
class PurposeConstraints:
    """
    Call constraints attached to a purpose node.
    Travel unchanged through traversal; every handler receives them.
    """
    step_name:   str                               # pipeline_trace step label
    max_tokens:  int   = 256
    timeout_s:   float = 8.0
    temperature: float = 0.1
    extra:       dict  = field(default_factory=dict)  # purpose-specific overrides


@dataclass
class Node:
    id:      str
    handler: Optional[Callable] = None     # None → routing node; callable → leaf handler

    @property
    def is_handler(self) -> bool:
        return self.handler is not None


@dataclass
class Edge:
    source:      str
    target:      str
    condition:   Callable[[InferenceContext], bool]
    priority:    int  = 0
    is_fallback: bool = False
    label:       str  = ""            # human-readable label for describe()


class RoutingError(RuntimeError):
    pass


# ── Gateway ──────────────────────────────────────────────────────────────────────

class InferenceGateway:
    def __init__(self) -> None:
        self._nodes:    dict[str, Node]              = {}
        self._edges:    dict[str, list[Edge]]        = {}
        self._purposes: dict[str, PurposeConstraints] = {}

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
            _lpt  = None
            _gtid = lambda: "?"

        constraints = self._purposes.get(purpose_id, PurposeConstraints(step_name=purpose_id))
        current_id  = purpose_id
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
                            _lpt(turn_id=_gtid(), step=constraints.step_name,
                                 elapsed_ms=ms, via=current_id)
                        except Exception:
                            pass
                    return result
                except Exception as exc:
                    ctx.last_elapsed_ms = round((time.monotonic() - t0) * 1000)
                    failed.add(current_id)
                    fallbacks = sorted(
                        [e for e in self._edges.get(current_id, [])
                         if e.is_fallback and e.target not in failed and e.condition(ctx)],
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
                [e for e in self._edges.get(current_id, [])
                 if not e.is_fallback and e.target not in failed and e.condition(ctx)],
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
            c    = self._purposes.get(node_id)
            c_str = (
                f"  [max_tokens={c.max_tokens} timeout={c.timeout_s}s temp={c.temperature}]"
                if c else ""
            )
            lines.append(f"  [{role}] {node_id}{c_str}")
            for e in sorted(self._edges.get(node_id, []), key=lambda e: e.priority):
                fb  = " [fallback]" if e.is_fallback else ""
                lbl = f" ({e.label})" if e.label else ""
                lines.append(f"    ──[pri={e.priority}{fb}]──▶  {e.target}{lbl}")
        return "\n".join(lines)


# ── Handler callables ────────────────────────────────────────────────────────────
# Accept (prompt, constraints, **kwargs) → str.
# Raise on any failure. Return non-empty string on success.
# Model + host read from constraints.extra so purpose drives configuration.

def _h_ollama(prompt: str, c: PurposeConstraints, **kw) -> str:
    """Raw Ollama /api/chat. Raises on any error or blank response."""
    model = kw.get("model") or c.extra.get("model") or os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
    host  = c.extra.get("host") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": c.temperature, "num_predict": c.max_tokens},
    }).encode()
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
    model = kw.get("model") or c.extra.get("or_model") or os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": c.temperature,
        "max_tokens": c.max_tokens,
    }).encode()
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
    """Cloud training active OR local unavailable."""
    return ctx.cloud_active or not ctx.local_available

def _cloud_ok(ctx: InferenceContext) -> bool:
    """OR balance above floor and API key present."""
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
        ("preparse", PurposeConstraints(
            step_name="preparse_search",
            max_tokens=120, timeout_s=5.0, temperature=0.1,
            extra={
                "model":    os.getenv("OLLAMA_REASONING_MODEL",
                                      os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")),
                "or_model": os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"),
            },
        )),
        ("winnow", PurposeConstraints(
            step_name="winnow",
            max_tokens=60, timeout_s=3.0, temperature=0.1,
            extra={
                "model":    os.getenv("IGOR_WINNOW_LOCAL_MODEL", "llama3.2:1b"),
                "or_model": os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"),
            },
        )),
        ("ne", PurposeConstraints(
            step_name="ne",
            max_tokens=1024, timeout_s=45.0, temperature=0.3,
            extra={
                "model":    os.getenv("IGOR_NE_LOCAL_MODEL", ""),
                "or_model": os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"),
            },
        )),
        ("think", PurposeConstraints(
            step_name="think_llm",
            max_tokens=80, timeout_s=8.0, temperature=0.2,
            extra={
                "model": os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),
            },
        )),
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
    gw.add_edge(Edge("preparse", "ollama_preparse", _local_preferred, priority=1,
                     label="local available, cloud_mode off"))
    gw.add_edge(Edge("preparse", "or_preparse",    _cloud_preferred,  priority=2,
                     label="cloud_mode active or local unavailable"))
    gw.add_edge(Edge("ollama_preparse", "or_preparse", _cloud_ok, priority=1,
                     is_fallback=True, label="ollama failed"))

    # ── Edges: winnow ────────────────────────────────────────────────────────
    gw.add_edge(Edge("winnow", "ollama_winnow", _local_preferred, priority=1,
                     label="local available, cloud_mode off"))
    gw.add_edge(Edge("winnow", "or_winnow",    _cloud_preferred,  priority=2,
                     label="cloud_mode active or local unavailable"))
    gw.add_edge(Edge("ollama_winnow", "or_winnow", _cloud_ok, priority=1,
                     is_fallback=True, label="ollama failed"))

    # ── Edges: ne ────────────────────────────────────────────────────────────
    gw.add_edge(Edge("ne", "or_ne",     _cloud_training, priority=1,
                     label="cloud_mode active (training — prefers cloud)"))
    gw.add_edge(Edge("ne", "ollama_ne", _ne_local_ok,    priority=2,
                     label="local NE model set, cloud_mode off"))
    gw.add_edge(Edge("ollama_ne", "or_ne", _cloud_ok, priority=1,
                     is_fallback=True, label="ollama_ne failed"))

    # ── Edges: think ─────────────────────────────────────────────────────────
    gw.add_edge(Edge("think", "ollama_think", _always, priority=1,
                     label="always local (think never hits cloud)"))
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
    )


# ── Module-level singleton ────────────────────────────────────────────────────────

_gateway: Optional[InferenceGateway] = None


def get_gateway() -> InferenceGateway:
    """Lazy singleton. First call builds the default gateway from env vars."""
    global _gateway
    if _gateway is None:
        _gateway = build_default_gateway()
    return _gateway
