"""inference_gateway.py — Unified inference routing as a DAG + tier ladder.

WHAT IT IS
──────────
All low-cost single-shot inference (winnow, NE, think) routes
through the gateway's DAG. Interactive reasoning (human turns, background
impulses, batch jobs) routes through gateway.reason() via the tier ladder.
The gateway is the ONLY entry point for all inference (D015 gateway-pattern).
No tier strings leak to callers. Routing policy lives in one file —
build_default_gateway() and _reason_with_failover(). Visibility:
gateway.describe() or /routing --dag.

WHY IT EXISTS
─────────────
OR spend is the burn-rate bottleneck. The gateway minimizes cloud escalation
by applying local-first (D211) with surgical cloud-only on high/medium-
complexity user turns. Budget gating (D071) and colocation avoidance (D205)
prevent silent cost explosion. Every tier attempt is logged via
forensic_logger; last_tier is inspectable post-hoc for auditing.

HOW IT WORKS (architecture)
───────────────────────────

DAG routing (gateway.call() for pipeline calls):
  Purpose nodes (entry)   — carry call constraints (max_tokens, timeout, model)
  Handler nodes (leaves)  — wrap a specific reasoner + model; raise on failure
  Edges (directed, condition-gated, priority-ordered)
    ├─ non-fallback: primary route (condition checked first, priority wins)
    └─ fallback:     only fire if handler raises; allow degradation

  Traversal: purpose → (evaluate edges in priority order) → handler.
  On handler failure: follow fallback edges. Raise RoutingError if no path.

  DAG purposes:
    winnow    — local Ollama → (fallback) → OR cheap
    ne        — (cloud_mode) OR cheap ↔ local Ollama
    think     — always local Ollama (no cloud fallback)

Tier ladder (gateway.reason() for interactive/background/batch):
  Local-first (D211):
    tier.2    Ollama qwen2.5:7b           — background, NE, batch; interactive fallback
    tier.3    OR gpt-4o-mini              — mechanical, no persona needed
    tier.3.5  OR Claude Haiku             — conversational, persona floor (D035)
    tier.4    OR Claude Sonnet            — high complexity, tools, milieu dominance

  Escalation gate (from thalamus.parsed_input):
    complexity=low  + is_user_turn=False → tier.3 (if cloud ok)
    complexity=med  + is_user_turn=True  → tier.3.5 OR tier.4
    complexity=high + is_user_turn=*     → tier.4
    research_mode=True + cloud_ok_override → escalate (D359)

Cloud availability gate (all three must pass, else fallback to local):
  - IGOR_CLOUD_TRAINING_ENABLED=true
  - OR balance ≥ IGOR_CLOUD_BUDGET_FLOOR_USD (default $10)
  - local hour 06:00-22:59, with D071 file-backed TTL override

  If balance is unknown (-1.0 sentinel), assume funded; never silently
  disable.

Three call profiles:
  level="interactive"      — human turn: cascade; cloud=Sonnet
  level="background"       — NE impulse: cloud=gpt-4o-mini if funded, else local
  level="background_batch" — proactive habits: always local, quality priority

InferenceContext (live routing state, passed to every edge condition):
  cloud_active       — is_cloud_training_active() time-of-day + intent check
  local_available    — Ollama health check passed
  balance_ok         — OR API key present + balance > floor
  is_background      — no latency requirement
  cloud_ok_override  — D071 file-backed TTL (night/local-only mode)
  is_user_turn       — D259: call is part of reply to human; gates complexity
  research_mode      — reading/extract path; allows escalation (D359)
  complexity         — low | medium | high (from thalamus.parsed_input)
  db_colocated       — D205: Postgres on same box as Ollama (deprioritizes local)

prompt_role (optional override, cloud-path only):
  Lets interactive turns request a leaner persona (analysis vs interactive).
  None → default role per tier.

Budget metering:
  tools/budget.py is_cloud_blocked() gates every cloud attempt. Reasons:
  OR balance, max-daily-spend, human-approval queue. Blocked → fall back to
  local tier.2. Every attempt logged to forensic_logger with turn_id tie-back.

Cache semantics:
  Anthropic prompt cache (OR Sonnet)  — per-reasoner, transparent
  Reasoning cache (D018)              — 12-min TTL + TWM watermark invalidation
  Ollama KV cache                     — per-machine, not controlled here

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D015  gateway pattern — policy in one file, not scattered
  D018  reasoning cache — 12-min TTL + TWM watermark invalidation
  D035  interactive persona tier — tier.3.5 (Haiku) between cheap + Sonnet
  D053  NE response format — response_format:json_object
  D071  cloud-ok runtime switch — file-backed TTL, not env var
  D198  primary reasoning interface — binary cloud/local + cascade
  D205  swarm hierarchy — db_colocated deprioritizes local
  D211  inference routing redesign — local-first, cloud only for high/med
  D234  tier-ladder update — Ollama primary, OR luxury (supersedes D073)
  D259  human-author routing — is_user_turn=True gates background escalation
  D327  inference encapsulation — ollama_reasoner + openrouter_reasoner
        consolidate 6 files
  D359  reading-extract via gateway — new reading_extract purpose

ENGRAM PORTION
──────────────
  PROC_SET_CLOUD_NOW    — human trigger; writes cloud_ok_override TTL
  PROC_NIGHT_READ       — threshold habit; clears override + drains local
  escalation_stats tool — tracks cloud escalations per topic

igor no longer owns a routing DAG or per-tier reasoners. reason() and call()
assemble a request and dispatch through the canonical Inference Proxy
(InferenceDevice); the Proxy's rules_engine owns source selection, budget, and
fallback (T-inf-reroute-A/B/C). Code asks for a tier (task_class), not a model;
a specific model is the experiment/comparison exception only and still rides
req.model through the Proxy.

If you want to change:
  - Purpose constraints  — edit build_default_gateway() (token/timeout/temp)
  - Purpose → task_class — edit _PURPOSE_TASK_CLASS
  - Source / model / budget routing — NOT here; it's the Inference Proxy's job
                          (devices/inference/: rules_engine, sources, budget_gate)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..igor_base import get_logger
from ..igor_base import IgorBase
from .forensic_logger import log_error

# ── Data model ──────────────────────────────────────────────────────────────────


@dataclass
class InferenceContext:
    """
    Routing-intent snapshot constructed before each gateway.call().

    The Proxy owns source selection now; what _call_via_proxy actually reads is
    is_user_turn / research_mode (→ foreground) and complexity. The cloud/local/
    balance fields are legacy carriers kept for caller compatibility.
    """

    cloud_active: bool  # legacy (Proxy owns cloud decisions)
    local_available: bool  # legacy
    balance_ok: bool  # legacy
    is_background: bool  # impulse / background turn (no latency requirement)
    cloud_ok_override: bool = True  # legacy
    last_elapsed_ms: float = 0.0  # set by gateway after each call
    db_colocated: bool = False  # legacy
    # routing intent signals (still consumed)
    is_user_turn: bool = False  # this call is part of a reply to a human
    research_mode: bool = False  # call chain is research (book reader, web extract)
    complexity: str = "low"  # low | medium | high — from thalamus parsed_input


@dataclass
class PurposeConstraints:
    """Call constraints attached to a purpose (token budget / timeout / temp).

    Mapped onto the InferenceRequest by _call_via_proxy; ``extra`` carries
    request extras such as ne's json response_format.
    """

    step_name: str  # pipeline_trace step label
    max_tokens: int = 256
    timeout_s: float = 8.0
    temperature: float = 0.1
    extra: dict = field(default_factory=dict)  # purpose-specific request extras


class RoutingError(RuntimeError):
    pass


# T-inf-reroute-B: purpose -> Proxy task_class. winnow / think are
# cheap mechanical extraction (minion); ne is narrative synthesis where quality
# matters (analyst); reading_extract is background book work (batch). The Proxy's
# rules_engine owns local-vs-cloud selection within each class.
_PURPOSE_TASK_CLASS = {
    "winnow": "minion",
    "ne": "analyst",
    "think": "minion",
    "reading_extract": "batch",
}


# ── Gateway ──────────────────────────────────────────────────────────────────────


class InferenceGateway(IgorBase):
    def __init__(self, inference=None) -> None:
        super().__init__()
        # Canonical Inference Proxy. igor requests inference like every
        # other device (reader/summarizer/evaluator pattern). Lazy-loaded
        # on first use; injectable for tests. (T-inf-reroute-A)
        self._inference = inference
        self._purposes: dict[str, PurposeConstraints] = {}
        self.last_tier: str = ""  # set after every reason() call
        self.last_elapsed_s: float = 0.0  # set after every reason() call

    # ── Registration ──────────────────────────────────────────────────────────

    def _get_inference(self):
        """Lazy-load the canonical Inference Proxy (reader/summarizer/
        evaluator pattern). Injectable via __init__ for tests."""
        if getattr(self, "_inference", None) is None:
            from unseen_university.devices.inference.device import InferenceDevice

            self._inference = InferenceDevice()
        return self._inference

    def register_purpose(self, node_id: str, constraints: PurposeConstraints) -> None:
        self._purposes[node_id] = constraints

    # ── Purpose calls ───────────────────────────────────────────────────────────

    def call(
        self,
        purpose_id: str,
        prompt: str,
        ctx: InferenceContext,
        **kwargs,
    ) -> str:
        """
        Run a purpose call through the canonical Inference Proxy, return the text.

        The purpose maps to a task_class; the Proxy's rules_engine owns local-vs-
        cloud selection and fallback. There is no routing DAG — igor requests
        inference like every other device (T-inf-reroute-B/C).

        Special kwargs (consumed here, not forwarded):
          model: str — request a specific model (testing / experiments /
            benchmark only; the sanctioned model-request exception). Still goes
            THROUGH the Proxy via req.model.
          timeout_override: float — override the purpose timeout (benchmark use).
        Raises RoutingError on dispatch failure or when no source is available.
        """
        timeout_override = kwargs.pop("timeout_override", None)
        model = kwargs.pop("model", "")

        constraints = self._purposes.get(
            purpose_id, PurposeConstraints(step_name=purpose_id)
        )
        if timeout_override is not None:
            from dataclasses import replace

            constraints = replace(constraints, timeout_s=float(timeout_override))

        return self._call_via_proxy(purpose_id, prompt, ctx, constraints, model=model)

    def _call_via_proxy(
        self,
        purpose_id: str,
        prompt: str,
        ctx: "InferenceContext",
        constraints: "PurposeConstraints",
        model: str = "",
    ) -> str:
        """Route a purpose call through the canonical Inference Proxy.

        Maps PurposeConstraints -> InferenceRequest (max_tokens / temperature /
        timeout, and response_format via extra) and the purpose -> a task_class.
        An explicit `model` (experiment/benchmark exception) is forwarded as
        req.model. Returns the response text; raises RoutingError on dispatch
        failure or when no source is available — matching call()'s failure
        contract so the ne / think callers keep their except-handling.
        """
        from unseen_university.devices.inference.shim import InferenceRequest

        task_class = _PURPOSE_TASK_CLASS.get(purpose_id, "minion")
        foreground = bool(
            getattr(ctx, "is_user_turn", False)
            or getattr(ctx, "research_mode", False)
        )
        extra: dict = {}
        if "response_format" in constraints.extra:
            extra["response_format"] = constraints.extra["response_format"]

        req = InferenceRequest(
            messages=[{"role": "user", "content": prompt}],
            model=model or "",
            max_tokens=constraints.max_tokens,
            temperature=constraints.temperature,
            timeout=int(constraints.timeout_s),
            task_class=task_class,
            foreground=foreground,
            extra=extra,
        )
        get_logger(__name__).info(
            "[gateway] call(%s) -> Proxy.dispatch task_class=%s foreground=%s model=%s",
            purpose_id,
            task_class,
            foreground,
            model or "(tier)",
        )
        try:
            resp = self._get_inference().dispatch(req)
        except Exception as exc:
            raise RoutingError(
                f"call('{purpose_id}'): Proxy dispatch failed: {exc}"
            ) from exc
        if not (resp.text or "").strip() and resp.source_kind == "none":
            raise RoutingError(
                f"call('{purpose_id}'): no inference source available"
            )
        ctx.last_elapsed_ms = float(getattr(resp, "elapsed_ms", 0) or 0)
        return resp.text

    # ── Visibility ────────────────────────────────────────────────────────────

    def describe(self) -> str:
        """Human-readable purpose table — used by /routing --dag.

        igor no longer owns a routing DAG; source selection and fallback are the
        Inference Proxy's job (rules_engine). This lists the purposes igor calls
        and the task_class each maps to.
        """
        lines = ["── Inference purposes (routed by the Inference Proxy) ──", ""]
        for purpose_id in sorted(self._purposes):
            c = self._purposes[purpose_id]
            tc = _PURPOSE_TASK_CLASS.get(purpose_id, "minion")
            lines.append(
                f"  {purpose_id}  → task_class={tc}  "
                f"[max_tokens={c.max_tokens} timeout={c.timeout_s}s temp={c.temperature}]"
            )
        lines.append("")
        lines.append("Source selection + fallback owned by the Proxy (rules_engine).")
        return "\n".join(lines)

    # ── Primary reasoning interface ────────────────────────────────────────────

    def reason(
        self,
        *args,
        **kwargs,
    ) -> "tuple[str, float, bool]":
        """Timing wrapper around _reason_impl. Updates last_elapsed_s."""
        import time as _time

        _t0 = _time.monotonic()
        try:
            return self._reason_impl(*args, **kwargs)
        finally:
            self.last_elapsed_s = _time.monotonic() - _t0
            global _last_latency_s
            _last_latency_s = self.last_elapsed_s

    def _reason_impl(
        self,
        user_input: str,
        relevant: list,
        core: list,
        *,
        level: str = "interactive",
        skip_to: str = "tier.3.5",  # deprecated -- ignored; kept for caller compat (D198)
        preparse_csb: str = "",
        thread_id: Optional[str] = None,
        cortex=None,
        instance_id: str = "",
        local_only: bool = False,
        on_tier: Optional[Callable[[str], None]] = None,
        is_user_turn: bool = False,
        complexity: str = "low",
        prompt_role: Optional[str] = None,
    ) -> "tuple[str, float, bool]":
        """
        Route a reasoning request through the canonical Inference Proxy.

        T-inf-reroute-A: igor is now a normal Proxy consumer. It assembles a
        message list and calls InferenceDevice.dispatch() exactly like reader,
        summarizer, and evaluator do. The old per-tier ladder (direct Ollama /
        OpenRouter reasoners, budget gating, tool loop, fall-through diagnostics)
        is gone -- the Proxy owns routing (rules_engine), budget (budget_gate),
        and source selection. igor's reasoning-specific behaviour is re-layered
        later (his specifics; he is asleep for this pass).

        The signature is FROZEN across the ~8 reason() callers. Params that were
        igor-ladder-specific (skip_to, local_only, on_tier, level) are accepted
        and mapped onto the request where meaningful, ignored otherwise.

        Returns (response_text, cost_usd, used_api). used_api is reconstructed
        from response.source_kind ("cloud" -> True; "local"/"none" -> False) --
        the signal T-proxy-source-kind added for exactly this purpose.
        """
        from unseen_university.devices.inference.shim import InferenceRequest
        from .system_prompt import build_system_prompt

        # -- Assemble messages the way every device does ----------------------
        system = build_system_prompt(
            cortex,
            instance_id or "wild-0001",
            role=prompt_role or "interactive",
        )
        context = ""
        if cortex is not None:
            try:
                context = build_twm_context(
                    cortex,
                    tier="tier.4" if is_user_turn else "tier.2",
                    thread_id=thread_id,
                    relevant_memories=relevant,
                )
            except Exception as _e:
                log_error(kind="TWM_CONTEXT_FAIL", detail=str(_e))

        parts = [p for p in (preparse_csb, user_input, context) if p]
        content = "\n\n".join(parts)
        messages = [{"role": "user", "content": content}]

        # -- Routing hints -- the Proxy's rules_engine owns the real decision -
        # task_class picks the candidate source pool; foreground flips the
        # billing preference toward usage_based (cloud). local_only is honoured
        # best-effort by declining foreground (prefer flat_rate / local); a hard
        # force-local belongs to igor's specifics, deferred.
        _level = str(level).lower()
        if "batch" in _level:
            task_class = "batch"
        elif "background" in _level:
            task_class = "minion"
        elif is_user_turn:
            task_class = "analyst"
        else:
            task_class = "worker"
        foreground = (not local_only) and (
            is_user_turn or complexity in ("medium", "high")
        )

        req = InferenceRequest(
            messages=messages,
            system=system,
            task_class=task_class,
            foreground=foreground,
            instance_id=instance_id,
            session_id=thread_id or "",
        )

        get_logger(__name__).info(
            "[gateway] reason -> Proxy.dispatch task_class=%s foreground=%s "
            "is_user_turn=%s local_only=%s",
            task_class,
            foreground,
            is_user_turn,
            local_only,
        )
        if on_tier:
            try:
                on_tier(f"proxy/{task_class}")
            except Exception as _e:
                log_error(kind="ON_TIER_FAIL", detail=str(_e))

        resp = self._get_inference().dispatch(req)

        used_api = resp.source_kind == "cloud"
        self.last_tier = f"proxy/{resp.source_kind}"
        return resp.text, resp.cost_estimate, used_api


# ── Default gateway factory ───────────────────────────────────────────────────────


def build_default_gateway() -> InferenceGateway:
    """Register the purpose constraints igor's call() uses.

    There is no routing DAG: the Inference Proxy (rules_engine) owns source
    selection and fallback. This only declares each purpose's call constraints
    (token budget / timeout / temperature, and ne's json response_format).
    """
    gw = InferenceGateway()
    purposes = [
        ("winnow", PurposeConstraints(
            step_name="winnow", max_tokens=60, timeout_s=3.0, temperature=0.1)),
        ("ne", PurposeConstraints(
            step_name="ne", max_tokens=1024, timeout_s=45.0, temperature=0.3,
            extra={"response_format": {"type": "json_object"}})),
        ("think", PurposeConstraints(
            step_name="think_llm", max_tokens=80, timeout_s=8.0, temperature=0.2)),
        ("reading_extract", PurposeConstraints(
            step_name="reading_extract", max_tokens=220, timeout_s=300.0, temperature=0.1)),
    ]
    for purpose_id, constraints in purposes:
        gw.register_purpose(purpose_id, constraints)
    return gw


# ── Context factory ───────────────────────────────────────────────────────────────


def make_context(
    is_background: bool = False,
    is_user_turn: bool = False,
    research_mode: bool = False,
    complexity: str = "low",
) -> InferenceContext:
    """Build a fresh InferenceContext carrying the call's routing intent.

    The Proxy owns source selection now, so this no longer probes Ollama/OR
    health or balance — it just carries is_user_turn / research_mode / complexity
    (the signals _call_via_proxy turns into the foreground/task_class decision).
    The remaining InferenceContext fields are legacy and left at defaults.
    """
    return InferenceContext(
        cloud_active=False,
        local_available=False,
        balance_ok=False,
        is_background=is_background,
        is_user_turn=is_user_turn,
        research_mode=research_mode,
        complexity=complexity,
    )


# ── Module-level singleton ────────────────────────────────────────────────────────

_gateway: Optional[InferenceGateway] = None
_last_latency_s: float = 0.0


def get_gateway() -> InferenceGateway:
    """Lazy singleton. First call builds the default gateway from env vars."""
    global _gateway
    if _gateway is None:
        _gateway = build_default_gateway()
    return _gateway


def get_last_latency_s() -> float:
    """Return elapsed seconds of the most recent gateway.reason() call."""
    return _last_latency_s


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
            detail=f"devices/igor/cognition/inference_gateway.py: {_bare_e}",
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
