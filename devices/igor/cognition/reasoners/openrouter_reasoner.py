"""
OpenRouter reasoner — OpenAI-compatible API to any cloud inference model.

Env vars:
    OPENROUTER_API_KEY          — API key from openrouter.ai
    OPENROUTER_DEFAULT_MODEL    — default model (default: openai/gpt-4o-mini)

Supports tool use via OpenAI function-calling format.
Prefix responses with [model-name] when show_model_tag=True.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

_HEARTBEAT_SECS = int(os.getenv("IGOR_CLOUD_HEARTBEAT_SECS", "45"))

from rich.console import Console

from ...memory.models import Memory
from ...tools.registry import registry
from ...tools.budget import check_budget_floor as _check_budget_floor
from ... import tools as _tools  # noqa: F401 — registers all tools
from .base import (
    BaseReasoner,
    MAX_TURNS,
    CONTEXT_WARN_CHARS,
    CONTEXT_HARD_CAP_CHARS,
    RESEARCH_TOOL_CAP,
    RESEARCH_MODE,
    BIG_READ_TOOLS,
    BASH_READ_PATTERNS,
    exit_requested,
)
from ..system_prompt import build_system_prompt
from ..forensic_logger import (
    log_reasoning_call,
    log_tool_call,
    log_inference_io,
    log_error,
)
from ...memory.scrub import scrub

console = Console()

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# _build_session_context and _build_memory_context live in BaseReasoner (WO8)


def preparse_via_openrouter(
    user_input: str,
    habits: list,
    model: str = "openai/gpt-4o-mini",
) -> str:
    """
    Run preparse via OpenRouter → PARSED_INPUT CSB block.
    Falls back to rule-based CSB on any error.
    Returns a CSB string (always — never raises).
    """
    from .ollama_reasoner import _PREPARSE_PROMPT, _rule_based_csb
    from ...memory.models import Memory as _Memory

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return _rule_based_csb(user_input, habits)

    prompt = _PREPARSE_PROMPT.format(text=user_input[:300])

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 120,
        }
    ).encode()
    req = urllib.request.Request(
        f"{OPENROUTER_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_REFERER,
        },
        method="POST",
    )

    fallback_reason = None
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        if "[PARSED_INPUT]" in text:
            return text
        fallback_reason = "no_parsed_input_block"
    except Exception as exc:
        console.print(
            f"[yellow][PREPARSE] OR preparse failed ({exc}), using rule-based fallback[/]"
        )
        fallback_reason = f"exception:{type(exc).__name__}"

    if fallback_reason:
        try:
            from ..forensic_logger import log_error

            log_error(
                kind="preparse_fallback",
                detail=fallback_reason,
                source="openrouter_reasoner",
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
            )

    return _rule_based_csb(user_input, habits)


# ── G53: Cloud-directed habit extraction ──────────────────────────────────────

_HABIT_EXTRACT_PROMPT = """\
You are building a cognitive tree for an AI agent named Igor.
Analyze this interaction and extract ONE node worth adding to the tree.

USER INPUT:
{user_input}

ASSISTANT RESPONSE (summary):
{response_summary}

TIER: {tier}

Three node types are possible — pick the BEST fit:

TYPE "procedural": A recurring trigger with a stable, automatable response.
  Good: greetings, status checks, "what time is it" → get_current_time, tool-dispatch patterns.
  JSON: {{"type":"procedural","trigger":"2-8 key words","narrative":"what this does and why",
         "code_ref":"tools.module:fn OR empty","response_template":"canned text OR empty",
         "confidence":0.0-1.0}}

TYPE "factual": A stable, generalizable fact or principle learned from this interaction.
  Good: architectural decisions, domain facts, "X works by Y", confirmed behaviors.
  Not: ephemeral context, session-specific state, things likely to change.
  JSON: {{"type":"factual","narrative":"1-2 sentences: the stable fact","confidence":0.0-1.0}}

TYPE "interpretive": A connection between this situation and Igor's core values (CP1-CP6).
  Good: "when X happens, it means Y about the situation, which connects to CP3/CP4/etc."
  CP1=epistemic honesty, CP2=failure is learning, CP3=follow the why, CP4=reduce friction,
  CP5=respect experience in all systems, CP6=safety must be built.
  JSON: {{"type":"interpretive","from_id":"CP1-CP6","narrative":"the meaning connection",
         "meaning_payload":"why this matters to Igor personally","confidence":0.0-1.0}}

If nothing generalizable — interaction is too specific, trivial, or already known:
SKIP

Respond with ONLY the JSON or SKIP. No markdown, no explanation."""


def _habit_extract_worker(
    user_input: str,
    response_text: str,
    cortex,
    tier: str,
) -> None:
    """
    G53: Fire-and-forget habit extraction from a cloud escalation.
    Runs in a daemon thread — never blocks the main response path.
    Uses gpt-4o-mini (cheap) to identify habitizable patterns.
    Stores discovered habits with source="cloud_directed" (G46 field).
    """
    import threading
    import uuid

    try:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return

        cheap_model = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
        prompt = _HABIT_EXTRACT_PROMPT.format(
            user_input=user_input[:400],
            response_summary=response_text[:300],
            tier=tier,
        )

        payload = json.dumps(
            {
                "model": cheap_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 200,
            }
        ).encode()
        req = urllib.request.Request(
            f"{OPENROUTER_BASE}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": OPENROUTER_REFERER,
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["choices"][0]["message"]["content"].strip()

        if result.upper().startswith("SKIP") or not result.startswith("{"):
            return

        node_data = json.loads(result)
        node_type = node_data.get("type", "procedural").strip().lower()
        narrative = node_data.get("narrative", "").strip()
        confidence = float(node_data.get("confidence", 0.5))

        if not narrative or confidence < 0.6:
            return

        from ...memory.models import Memory as _Memory, MemoryType as _MT

        if node_type == "procedural":
            trigger = node_data.get("trigger", "").strip()
            if not trigger:
                return
            # Skip if close duplicate exists
            existing = cortex.search(trigger, limit=3)
            for mem in existing:
                if (
                    mem.metadata.get("trigger")
                    and trigger.split()[0] in mem.metadata["trigger"]
                ):
                    return
            code_ref = node_data.get("code_ref", "").strip()
            resp_tmpl = node_data.get("response_template", "").strip()
            metadata = {
                "trigger": trigger,
                "cloud_directed": True,
                "extraction_tier": tier,
            }
            if code_ref:
                # Validate code_ref before storing — phantom habits that reference
                # missing tools fire and error on every matching turn.
                # code_ref format: "module.path:tool_name" — we check the tool_name
                # against the registry (same lookup path used at dispatch time).
                _tool_name = code_ref.split(":")[-1] if ":" in code_ref else code_ref
                from ..tools.registry import registry as _cr_registry

                if _cr_registry.get(_tool_name) is not None:
                    metadata["code_ref"] = code_ref
                else:
                    log_error(
                        kind="CODE_REF_INVALID",
                        detail=(
                            f"cloud-extracted habit skipped invalid code_ref "
                            f"'{code_ref}' (tool '{_tool_name}' not in registry)"
                        ),
                    )
            if resp_tmpl:
                metadata["response_template"] = resp_tmpl
            mem = _Memory(
                id=f"PROC_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.PROCEDURAL,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}|trigger={trigger[:40]}",
                metadata=metadata,
            )
            cortex.store(mem)
            cortex.add_child("CP2", mem.id)

        elif node_type == "factual":
            mem = _Memory(
                id=f"FACT_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.FACTUAL,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}",
                metadata={"cloud_directed": True, "extraction_tier": tier},
            )
            cortex.store(mem)
            cortex.add_child("CP3", mem.id)  # "there's always a why" → facts

        elif node_type == "interpretive":
            from_id = node_data.get("from_id", "").strip()
            meaning_pay = node_data.get("meaning_payload", "").strip()
            if not from_id:
                return
            mem = _Memory(
                id=f"INTERP_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.INTERPRETIVE,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}|from={from_id}",
                metadata={
                    "from_id": from_id,
                    "cloud_directed": True,
                    "extraction_tier": tier,
                },
            )
            cortex.store(mem)
            if cortex.get(from_id):
                cortex.add_child(from_id, mem.id)
                if meaning_pay:
                    cortex.add_interpretive_edge(
                        from_id=from_id,
                        to_id=mem.id,
                        direction="activation",
                        condition_csb=f"cloud_extracted|tier={tier}",
                        meaning_payload=meaning_pay,
                        action_pointer="",
                    )
        else:
            return

        try:
            from ..forensic_logger import log_memory_op as _lm

            _lm(
                operation="cloud_node_extracted",
                memory_type=node_type,
                narrative_snippet=f"tier={tier}|conf={confidence:.2f}|id={mem.id}",
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
            )

        console.print(
            f"[dim cyan][G53] {node_type} node from {tier}: {mem.id} "
            f"conf={confidence:.2f} — {narrative[:60]}[/]"
        )

    except json.JSONDecodeError as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
        )
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
        )


class OpenRouterReasoner(BaseReasoner):
    """Reason via any model accessible through OpenRouter's OpenAI-compatible API."""

    def __init__(self, model: str | None = None, show_model_tag: bool = False):
        raw = model or os.getenv("OPENROUTER_DEFAULT_MODEL", DEFAULT_MODEL)
        self.model = raw
        self.show_model_tag = show_model_tag

    def name(self) -> str:
        return f"OpenRouter/{self.model}"

    def set_model(self, model: str) -> str:
        self.model = model
        return self.model

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        cortex=None,
        preparse_csb: str = "",
        thread_id: str | None = None,
        no_tools: bool = False,
    ) -> tuple[str, float]:
        """Run full agentic tool loop via OpenRouter."""
        t0 = time.perf_counter()

        # WO1: dynamic system prompt from cortex memories (full persona — human turn)
        system = build_system_prompt(cortex, instance_id, role="interactive")

        # ── Context winnow: targeted retrieval before main call ───────────────
        # Cheap pre-call identifies which specific memories are needed.
        # Merges results with passed-in relevant_memories (deduped).
        try:
            from ..basal_ganglia import _word_graph as _wg

            _winnowed = self._winnow_context(user_input, cortex, word_graph=_wg)
            if _winnowed:
                seen = {m.id for m in relevant_memories}
                relevant_memories = list(relevant_memories) + [
                    m for m in _winnowed if m.id not in seen
                ]
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
            )

        # ── Blob expansion: append full content for high-relevance blob memories ─
        if cortex is not None:
            try:
                cortex.expand_blob_memories(relevant_memories)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
                )

        content = user_input
        if preparse_csb:
            content = preparse_csb + "\n\n" + content
        session_ctx = self._build_session_context(cortex, thread_id=thread_id)
        mem_ctx = self._build_memory_context(relevant_memories)
        if session_ctx:
            content += session_ctx
        if mem_ctx:
            content += mem_ctx
        content = scrub(content)
        _context_chars = len(system) + len(content)  # G55: layer boundary metric
        # Infer tier from model name for logging
        _m = self.model.lower()
        _tier = (
            "tier.3.5"
            if "haiku" in _m
            else "tier.4" if "sonnet" in _m or "opus" in _m else "tier.3"
        )

        messages = [{"role": "user", "content": content}]
        # #301: background/impulse calls never use tools — passing all 150+ tools causes
        # provider 400 "tools array too long". Skip entirely when no_tools=True.
        tools = [] if no_tools else registry.to_openai_schemas()
        total_cost = 0.0
        turn = 0
        big_read_count = 0

        while True:
            turn += 1

            # ── EXIT INTERRUPT — stop at turn boundary if /exit was typed ─
            if exit_requested.is_set():
                console.print(
                    "[yellow][OR] Exit requested — stopping at turn boundary.[/]"
                )
                return "Stopping — exit requested.", total_cost

            # ── TURN LIMIT — safety backstop (budget floor is primary gate) ──
            # MAX_TURNS=0 means unlimited (for reading sessions etc.)
            if MAX_TURNS > 0 and turn > MAX_TURNS:
                console.print(
                    f"[yellow][OR] MAX_TURNS ({MAX_TURNS}) reached — stopping tool loop.[/]"
                )
                break

            # ── BUDGET FLOOR — stop when account drops below configured floor
            _floor_ok, _floor_msg = _check_budget_floor()
            if not _floor_ok:
                console.print(f"[yellow][OR] {_floor_msg}[/]")
                break

            # ── CONTEXT SIZE WARNING + HARD CAP (#26) ─────────────────────
            ctx_chars = self._messages_total_chars(messages)
            if ctx_chars > CONTEXT_HARD_CAP_CHARS:
                messages = self._trim_messages(messages)
                ctx_chars = self._messages_total_chars(messages)
                console.print(
                    f"[yellow][OR] context trimmed to ~{ctx_chars // 1000}K chars at turn {turn}[/]"
                )
                try:
                    from ..forensic_logger import log_anomaly as _la

                    _la(
                        kind="CONTEXT_OVERFLOW",
                        detail=f"model={self._model()}|turn={turn}|trimmed_to={ctx_chars}",
                    )
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
                    )
            elif ctx_chars > CONTEXT_WARN_CHARS:
                console.print(
                    f"[yellow][OR] context ~{ctx_chars // 1000}K chars at turn {turn} "
                    f"— consider breaking into smaller steps[/]"
                )

            _hb = threading.Timer(
                _HEARTBEAT_SECS,
                lambda _t=turn: console.print(
                    f"[dim yellow][OR] Still thinking... (turn {_t}, cloud reasoning in progress)[/]"
                ),
            )
            _hb.daemon = True
            _hb.start()
            try:
                response = self._call_api(messages, tools, system=system)
            finally:
                _hb.cancel()
            choice = response["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")
            total_cost += self._estimate_cost(response.get("usage", {}))

            if finish_reason in ("stop", "end_turn", None) or (
                not msg.get("tool_calls") and finish_reason != "tool_calls"
            ):
                text = msg.get("content") or ""
                if self.show_model_tag:
                    text = f"[{self.model}] {text}"
                usage = response.get("usage", {})
                _elapsed_ms = int((time.perf_counter() - t0) * 1000)
                log_reasoning_call(
                    provider="openrouter",
                    model=self.model,
                    tier=_tier,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    context_chars=_context_chars,
                    cost_usd=total_cost,
                    elapsed_ms=_elapsed_ms,
                    turns=turn,
                    response_summary=text[:120],
                )
                log_inference_io(
                    provider="openrouter",
                    model=self.model,
                    tier=_tier,
                    prompt=system + "\n\n" + content,
                    response=text,
                    elapsed_ms=_elapsed_ms,
                    call_type="reason",
                )
                # G53: cloud-directed habit extraction — daemon thread, never blocks
                if (
                    cortex is not None
                    and _tier in ("tier.3", "tier.3.5", "tier.4")
                    and os.getenv("IGOR_HABIT_EXTRACT", "true").lower()
                    not in ("0", "false", "no")
                ):
                    import threading as _threading

                    _t = _threading.Thread(
                        target=_habit_extract_worker,
                        args=(user_input, text, cortex, _tier),
                        daemon=True,
                        name="habit_extractor",
                    )
                    _t.start()
                return text, total_cost

            elif finish_reason == "tool_calls" or msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", [])
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                _research_blocked = False
                for tc in tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]

                    try:
                        kwargs = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        kwargs = {}

                    # ── RESEARCH GATE ──────────────────────────────────────
                    _is_bash_read = tool_name == "run_bash" and any(
                        kwargs.get("command", "").lstrip().startswith(p)
                        for p in BASH_READ_PATTERNS
                    )
                    if tool_name in BIG_READ_TOOLS or _is_bash_read:
                        big_read_count += 1
                        _cap = int(
                            os.getenv("IGOR_RESEARCH_TOOL_CAP", str(RESEARCH_TOOL_CAP))
                        )
                        _mode = os.getenv("IGOR_RESEARCH_MODE", "false").lower() in (
                            "1",
                            "true",
                            "yes",
                        )
                        if big_read_count > _cap and not _mode:
                            console.print(
                                f"[yellow][OR] Research tool cap ({_cap}) reached — "
                                f"{tool_name} call #{big_read_count} blocked. "
                                f"Set IGOR_RESEARCH_MODE=true to allow bulk reading.[/]"
                            )
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": "BLOCKED: research tool cap reached — set IGOR_RESEARCH_MODE=true",
                                }
                            )
                            _research_blocked = True
                            break

                    input_summary = ", ".join(
                        f"{k}={str(v)[:40]!r}" for k, v in kwargs.items()
                    )
                    t_tool = time.perf_counter()
                    result = registry.execute(tool_name, kwargs)
                    tool_elapsed = int((time.perf_counter() - t_tool) * 1000)
                    result_preview = str(result)[:120].replace("\n", " ")
                    self.print_tool_call(
                        "OR", turn, tool_name, input_summary, result_preview
                    )
                    log_tool_call(
                        tool_name=tool_name,
                        args_summary=input_summary,
                        result_summary=result_preview,
                        success=not result_preview.startswith("Error"),
                        elapsed_ms=tool_elapsed,
                    )

                    if cortex is not None:
                        cortex.write_ring(
                            f"TOOL:{tool_name} input={input_summary} result={result_preview}",
                            category="tool_trace",
                        )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": self._cap_tool_result(str(result)),
                        }
                    )

                if _research_blocked:
                    _cap = int(
                        os.getenv("IGOR_RESEARCH_TOOL_CAP", str(RESEARCH_TOOL_CAP))
                    )
                    return (
                        f"⚠ Research tool cap ({_cap} big-read calls) reached. "
                        f"Set IGOR_RESEARCH_MODE=true if bulk reading is needed.",
                        total_cost,
                    )

            else:
                text = msg.get("content") or f"[Stopped: {finish_reason}]"
                if self.show_model_tag:
                    text = f"[{self.model}] {text}"
                return text, total_cost

        # MAX_TURNS break lands here — return a graceful message rather than None
        return (
            "I reached my tool-use limit for this turn and stopped. "
            "You can ask me to continue or break the task into smaller steps.",
            total_cost,
        )

    def _call_api(self, messages: list, tools: list, system: str = "") -> dict:
        """POST to OpenRouter chat completions endpoint."""
        token = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not token:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        # Guard: models that don't support assistant prefill require the final
        # message to have role=user or role=tool.  If messages ends with an
        # assistant role (e.g. due to tool-call exception mid-loop), append a
        # synthetic user nudge rather than sending a 400-guaranteed payload.
        if messages and messages[-1].get("role") == "assistant":
            messages = messages + [
                {
                    "role": "user",
                    "content": "[tool results unavailable — please continue]",
                }
            ]

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "tools": tools if tools else None,
            "max_tokens": 4096,
        }
        if not tools:
            del payload["tools"]

        body = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": "Igor-Wild-Agent",
        }
        req = urllib.request.Request(
            f"{OPENROUTER_BASE}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_text = e.read().decode()[:300]
            raise RuntimeError(f"OpenRouter API error {e.code}: {err_text}")

    def _estimate_cost(self, usage: dict) -> float:
        """Best-effort cost estimate based on model name."""
        inp = usage.get("prompt_tokens", 0)
        out = usage.get("completion_tokens", 0)
        m = self.model.lower()
        if "claude-sonnet-4" in m or "claude-sonnet-4-6" in m:
            # OpenRouter adds ~5% margin over Anthropic direct
            return inp * 0.00000315 + out * 0.00001575
        if "claude-haiku" in m or "haiku" in m:
            return inp * 0.00000084 + out * 0.0000042
        if "claude-opus" in m:
            return inp * 0.0000159 + out * 0.0000795
        if "gpt-4o-mini" in m:
            return inp * 0.00000015 + out * 0.0000006
        if "gpt-4o" in m:
            return inp * 0.0000025 + out * 0.00001
        if "deepseek" in m:
            return inp * 0.00000014 + out * 0.00000028
        if "mistral" in m or "mixtral" in m:
            return inp * 0.0000002 + out * 0.0000006
        # Generic estimate for unknown models
        return inp * 0.000001 + out * 0.000002
