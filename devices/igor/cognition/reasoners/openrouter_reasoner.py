"""
OpenRouter reasoner — OpenAI-compatible API to any upstream model.

Env vars:
    OPENROUTER_API_KEY          — API key from openrouter.ai
    OPENROUTER_DEFAULT_MODEL    — default model (default: openai/gpt-4o-mini)

Supports tool use via OpenAI function-calling format.
Prefix responses with [model-name] when show_model_tag=True.
"""

import json
import os
import time
import urllib.request
import urllib.error

from rich.console import Console

from ...memory.models import Memory
from ...tools.registry import registry
from ... import tools as _tools  # noqa: F401 — registers all tools
from .base import (BaseReasoner, MAX_TURNS, CONTEXT_WARN_CHARS, CONTEXT_HARD_CAP_CHARS,
                   CALL_COST_WARN_USD, RESEARCH_TOOL_CAP, RESEARCH_MODE, BIG_READ_TOOLS,
                   BASH_READ_PATTERNS, exit_requested)
from ..system_prompt import build_system_prompt
from ..forensic_logger import log_reasoning_call, log_tool_call
from ...memory.scrub import scrub

console = Console()

DEFAULT_MODEL      = "anthropic/claude-sonnet-4-6"
OPENROUTER_BASE    = "https://openrouter.ai/api/v1"
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

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 120,
    }).encode()
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
        console.print(f"[yellow][PREPARSE] OR preparse failed ({exc}), using rule-based fallback[/]")
        fallback_reason = f"exception:{type(exc).__name__}"

    if fallback_reason:
        try:
            from ..forensic_logger import log_error
            log_error(kind="preparse_fallback", detail=fallback_reason, source="openrouter_reasoner")
        except Exception:
            pass

    return _rule_based_csb(user_input, habits)


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
    ) -> tuple[str, float]:
        """Run full agentic tool loop via OpenRouter."""
        t0 = time.perf_counter()

        # WO1: dynamic system prompt from cortex memories
        system = build_system_prompt(cortex, instance_id)

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
        except Exception:
            pass

        content = user_input
        if preparse_csb:
            content = preparse_csb + "\n\n" + content
        session_ctx = self._build_session_context(cortex)
        mem_ctx = self._build_memory_context(relevant_memories)
        if session_ctx:
            content += session_ctx
        if mem_ctx:
            content += mem_ctx
        content = scrub(content)

        messages = [{"role": "user", "content": content}]
        tools = registry.to_openai_schemas()
        total_cost = 0.0
        turn = 0
        big_read_count = 0

        while True:
            turn += 1

            # ── EXIT INTERRUPT — stop at turn boundary if /exit was typed ─
            if exit_requested.is_set():
                console.print("[yellow][OR] Exit requested — stopping at turn boundary.[/]")
                return "Stopping — exit requested.", total_cost

            # ── TURN LIMIT — break runaway tool loops ─────────────────────
            if turn > MAX_TURNS:
                console.print(
                    f"[yellow][OR] MAX_TURNS ({MAX_TURNS}) reached — stopping tool loop.[/]"
                )
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
                    _la(kind="CONTEXT_OVERFLOW", detail=f"model={self._model()}|turn={turn}|trimmed_to={ctx_chars}")
                except Exception:
                    pass
            elif ctx_chars > CONTEXT_WARN_CHARS:
                console.print(
                    f"[yellow][OR] context ~{ctx_chars // 1000}K chars at turn {turn} "
                    f"— consider breaking into smaller steps[/]"
                )

            response = self._call_api(messages, tools, system=system)
            choice = response["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")
            total_cost += self._estimate_cost(response.get("usage", {}))

            # ── PER-CALL COST CAP ─────────────────────────────────────────
            _cost_cap = float(os.getenv("IGOR_CALL_COST_WARN_USD", str(CALL_COST_WARN_USD)))
            if total_cost > _cost_cap:
                console.print(
                    f"[yellow][OR] Per-call cost cap ${_cost_cap:.2f} hit "
                    f"(${total_cost:.4f} at turn {turn}) — stopping. "
                    f"Raise IGOR_CALL_COST_WARN_USD or ask Akien.[/]"
                )
                try:
                    from ..forensic_logger import log_anomaly as _la
                    _la(kind="COST_CAP_HIT", detail=f"model={self.model}|cost={total_cost:.4f}|cap={_cost_cap}|turn={turn}")
                except Exception:
                    pass
                return (
                    f"⚠ Per-call cost cap ${_cost_cap:.2f} reached (${total_cost:.4f} at turn {turn}). "
                    f"Ask Akien to raise IGOR_CALL_COST_WARN_USD if deeper work is needed.",
                    total_cost,
                )

            if finish_reason in ("stop", "end_turn", None) or (
                not msg.get("tool_calls") and finish_reason != "tool_calls"
            ):
                text = msg.get("content") or ""
                if self.show_model_tag:
                    text = f"[{self.model}] {text}"
                usage = response.get("usage", {})
                log_reasoning_call(
                    provider="openrouter", model=self.model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    cost_usd=total_cost,
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    turns=turn, response_summary=text[:120],
                )
                return text, total_cost

            elif finish_reason == "tool_calls" or msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", [])
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                })

                _research_blocked = False
                for tc in tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]

                    try:
                        kwargs = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        kwargs = {}

                    # ── RESEARCH GATE ──────────────────────────────────────
                    _is_bash_read = (
                        tool_name == "run_bash"
                        and any(kwargs.get("command", "").lstrip().startswith(p)
                                for p in BASH_READ_PATTERNS)
                    )
                    if tool_name in BIG_READ_TOOLS or _is_bash_read:
                        big_read_count += 1
                        _cap = int(os.getenv("IGOR_RESEARCH_TOOL_CAP", str(RESEARCH_TOOL_CAP)))
                        _mode = os.getenv("IGOR_RESEARCH_MODE", "false").lower() in ("1", "true", "yes")
                        if big_read_count > _cap and not _mode:
                            console.print(
                                f"[yellow][OR] Research tool cap ({_cap}) reached — "
                                f"{tool_name} call #{big_read_count} blocked. "
                                f"Set IGOR_RESEARCH_MODE=true to allow bulk reading.[/]"
                            )
                            messages.append({"role": "tool", "tool_call_id": tc["id"],
                                             "content": "BLOCKED: research tool cap reached — set IGOR_RESEARCH_MODE=true"})
                            _research_blocked = True
                            break

                    input_summary = ", ".join(
                        f"{k}={str(v)[:40]!r}" for k, v in kwargs.items()
                    )
                    t_tool = time.perf_counter()
                    result = registry.execute(tool_name, kwargs)
                    tool_elapsed = int((time.perf_counter() - t_tool) * 1000)
                    result_preview = str(result)[:120].replace("\n", " ")
                    self.print_tool_call("OR", turn, tool_name, input_summary, result_preview)
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

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": self._cap_tool_result(str(result)),
                    })

                if _research_blocked:
                    _cap = int(os.getenv("IGOR_RESEARCH_TOOL_CAP", str(RESEARCH_TOOL_CAP)))
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

    def _call_api(self, messages: list, tools: list, system: str = "") -> dict:
        """POST to OpenRouter chat completions endpoint."""
        token = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not token:
            raise RuntimeError("OPENROUTER_API_KEY not set")

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
        inp  = usage.get("prompt_tokens", 0)
        out  = usage.get("completion_tokens", 0)
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
