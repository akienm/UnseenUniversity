"""
Anthropic API reasoner.
Uses native tool_use protocol. Runs the full agentic tool loop.

Deep thinking is now visible: each tool call and its result are printed
to the console and optionally written to ring_memory. This satisfies the
"show more during deep thinking" agreement with akienm.

Short-term session context (ring memory) is injected into the prompt so
Igor has continuity within a conversation, not just long-term memory hits.

Budget tracking: before each API call, check remaining budget and warn/block
if needed. After each call, record the cost. Interruptors run post-response.
"""

import os
import time
from anthropic import Anthropic
from rich.console import Console
from ...memory.models import Memory
from ...tools.registry import registry
from ... import tools as _tools  # noqa: F401 - imports all tools, registers them
from .base import APIReasoner, MAX_TURNS, CONTEXT_WARN_CHARS
from ..system_prompt import build_system_prompt
from ..forensic_logger import log_reasoning_call, log_tool_call
from ...memory.scrub import scrub

console = Console()

DEFAULT_MODEL = "claude-sonnet-4-6"

# Convenient short names → full model IDs
MODEL_ALIASES: dict[str, str] = {
    "sonnet":  "claude-sonnet-4-6",
    "opus":    "claude-opus-4-6",
    "haiku":   "claude-haiku-4-5-20251001",
    "sonnet4": "claude-sonnet-4-6",
    "opus4":   "claude-opus-4-6",
    "haiku4":  "claude-haiku-4-5-20251001",
}

DEBUG_BYPASS_MODEL = "claude-haiku-4-5-20251001"

# Tools that indicate Igor is doing self-inspection/editing — switch to Haiku automatically
SELF_EDIT_TRIGGER_TOOLS = {"read_source_file", "list_source_files"}

# _build_session_context and _build_memory_context live in BaseReasoner (WO8)


class AnthropicReasoner(APIReasoner):

    def __init__(self, model: str | None = None):
        self._client = None
        raw = model or os.getenv("IGOR_MODEL", DEFAULT_MODEL)
        self.model = MODEL_ALIASES.get(raw, raw)
        self._debug_bypass = False   # When True, use Haiku regardless of self.model

    def _get_client(self):
        if self._client is None:
            self._client = Anthropic()
        return self._client

    def set_model(self, name: str) -> str:
        """Switch model at runtime. Accepts aliases or full IDs. Returns resolved name."""
        self.model = MODEL_ALIASES.get(name, name)
        self._client = None   # Force new client on next call (picks up any env changes)
        return self.model

    def set_debug_bypass(self, enabled: bool) -> str:
        """Enable or disable debug bypass mode. Returns status string."""
        self._debug_bypass = enabled
        if enabled:
            return f"DEBUG BYPASS ON → using {DEBUG_BYPASS_MODEL} (fast/cheap)"
        else:
            return f"DEBUG BYPASS OFF → back to {self.model}"

    @property
    def active_model(self) -> str:
        """The model that will actually be used on the next call."""
        return DEBUG_BYPASS_MODEL if self._debug_bypass else self.model

    def name(self) -> str:
        if self._debug_bypass:
            return f"Anthropic/{self.model} [DEBUG→{DEBUG_BYPASS_MODEL}]"
        return f"Anthropic/{self.model}"

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        cortex=None,
        preparse_csb: str = "",
    ) -> tuple[str, float]:
        """
        Run the full agentic tool loop.
        cortex is optional — if provided, tool calls are written to ring_memory
        and the recent ring entries are injected as session context.
        preparse_csb: structured PARSED_INPUT block prepended to user content.
        """
        t0 = time.perf_counter()
        total_input_tokens = 0
        total_output_tokens = 0

        memory_context = self._build_memory_context(relevant_memories)
        session_context = self._build_session_context(cortex)

        # WO1: dynamic system prompt from cortex memories
        system = build_system_prompt(cortex, instance_id)

        content = user_input
        if preparse_csb:
            content = preparse_csb + "\n\n" + content
        if session_context:
            content += session_context
        if memory_context:
            content += memory_context
        content = scrub(content)

        messages = [{"role": "user", "content": content}]
        tools = registry.to_anthropic_schemas()
        total_cost = 0.0
        tool_calls_made = []
        in_self_edit_session = False
        model_to_use = self.active_model
        turn = 0

        while True:
            turn += 1

            # ── TURN LIMIT — break runaway tool loops ─────────────────────
            if turn > MAX_TURNS:
                console.print(
                    f"[yellow][THINK] MAX_TURNS ({MAX_TURNS}) reached — stopping tool loop.[/]"
                )
                break

            # ── CONTEXT SIZE WARNING ───────────────────────────────────────
            ctx_chars = self._messages_total_chars(messages)
            if ctx_chars > CONTEXT_WARN_CHARS:
                console.print(
                    f"[yellow][THINK] context ~{ctx_chars // 1000}K chars at turn {turn} "
                    f"— consider breaking into smaller steps[/]"
                )

            # ── BUDGET CHECK before each API call ─────────────────────────
            try:
                from ...tools.budget import check_before_call, record_spend
                ok, budget_msg = check_before_call()
                if not ok:
                    console.print(f"[bold red][BUDGET] {budget_msg}[/]")
                    self._run_interruptors(cortex)
                    # Raise so failover chain in main.py can try OpenRouter/local
                    raise RuntimeError(f"Budget exhausted: {budget_msg}")
                elif "critical" in budget_msg.lower() or "low" in budget_msg.lower():
                    console.print(f"[yellow][BUDGET] {budget_msg}[/]")
            except ImportError:
                record_spend = None  # Budget module not available

            response = self._get_client().messages.create(
                model=model_to_use,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )

            call_cost = self._estimate_cost(response.usage, model_to_use)
            total_cost += call_cost
            total_input_tokens  += getattr(response.usage, "input_tokens", 0)
            total_output_tokens += getattr(response.usage, "output_tokens", 0)

            # ── RECORD SPEND ──────────────────────────────────────────────
            try:
                from ...tools.budget import record_spend as _rs
                _rs(call_cost, model=model_to_use, note=f"turn={turn}")
            except Exception:
                pass  # Budget tracker optional — don't crash reasoning

            if response.stop_reason == "end_turn":
                text = self._extract_text(response)
                if tool_calls_made:
                    console.print(f"[dim][THINK] Done. Tools used: {', '.join(tool_calls_made)}[/]")
                log_reasoning_call(
                    provider="anthropic", model=model_to_use,
                    input_tokens=total_input_tokens, output_tokens=total_output_tokens,
                    cost_usd=total_cost, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    turns=turn, response_summary=text[:120],
                )

                # ── Ethics gate (change.27) ────────────────────────────────
                if cortex is not None:
                    try:
                        from ...brainstem.core_patterns import validate_against_core
                        ok, reason = validate_against_core(text, cortex)
                        if not ok:
                            cortex.write_ring(
                                f"ETHICS_GATE|FAIL|{reason[:300]}|preview={text[:100]}",
                                category="ethics_gate",
                            )
                            console.print(f"[bold red][ETHICS GATE] Violation: {reason[:200]}[/]")
                            # Change 4: push to TWM with urgency=0.9 (ethics violations are urgent)
                            try:
                                cortex.twm_push(
                                    source="ethics_gate",
                                    content_csb=f"ETHICS_VIOLATION|{reason[:300]}|preview={text[:80]}",
                                    salience=0.9,
                                    metadata={"type": "ethics_flag", "reason": reason[:300]},
                                    ttl_seconds=3600,
                                    urgency=0.9,
                                )
                            except Exception:
                                pass
                            # change.33: submit to arbiter for akien's awareness (non-blocking)
                            try:
                                from ...arbiter import queue as arbiter_queue
                                arbiter_queue.submit(
                                    description=f"Response flagged by ethics gate: {text[:200]}",
                                    context=f"Violation: {reason[:300]}",
                                    action_type="ethics_flag",
                                    threshold_reason=reason[:200],
                                    metadata={"response_preview": text[:200]},
                                )
                            except Exception:
                                pass  # Arbiter submit must never block reasoning
                    except Exception:
                        pass  # Ethics gate must never crash the reasoning loop

                # ── Signal B (Change 3): extend TWM TTL on positive valence ──
                # If response valence ≥ 0.3, the high-urgency TWM obs we included
                # in context were confirmed useful — extend their TTL.
                if cortex is not None:
                    try:
                        self._extend_twm_on_positive_valence(text, cortex)
                    except Exception:
                        pass  # Signal B must never block reasoning

                # ── Run interruptors after final response ─────────────────
                self._run_interruptors(cortex)
                return text, total_cost

            elif response.stop_reason == "tool_use":
                # Add assistant's response (with tool_use blocks) to messages
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls_made.append(block.name)

                        # ── AUTO-HAIKU for self-edit sessions ─────────────────
                        if not in_self_edit_session and block.name in SELF_EDIT_TRIGGER_TOOLS:
                            in_self_edit_session = True
                            model_to_use = DEBUG_BYPASS_MODEL
                            console.print(
                                f"[dim][THINK] Self-edit detected ({block.name}) "
                                f"→ auto-switching to Haiku for this reasoning session[/]"
                            )

                        # ── DEEP THINKING VISIBILITY ──────────────────────────
                        # Show what I'm about to do (truncate large inputs for readability)
                        input_summary = self._summarize_input(block.input)
                        console.print(
                            f"[dim][THINK turn={turn}] ⚙ {block.name}({input_summary})[/]"
                        )

                        t_tool = time.perf_counter()
                        result = registry.execute(block.name, block.input)
                        tool_elapsed = int((time.perf_counter() - t_tool) * 1000)

                        # Show truncated result
                        result_preview = str(result)[:120].replace("\n", " ")
                        console.print(f"[dim][THINK turn={turn}]   → {result_preview}[/]")

                        log_tool_call(
                            tool_name=block.name,
                            args_summary=input_summary,
                            result_summary=result_preview,
                            success=not result_preview.startswith("Error"),
                            elapsed_ms=tool_elapsed,
                        )

                        # Write to ring_memory if cortex available
                        if cortex is not None:
                            ring_entry = (
                                f"TOOL:{block.name} "
                                f"input={input_summary} "
                                f"result={result_preview}"
                            )
                            cortex.write_ring(ring_entry, category="tool_trace")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": self._cap_tool_result(str(result)),
                        })

                # Feed results back and loop
                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason - return what we have
                text = self._extract_text(response) or f"[Stopped: {response.stop_reason}]"
                console.print(f"[yellow][THINK] Unexpected stop_reason={response.stop_reason}[/]")
                log_reasoning_call(
                    provider="anthropic", model=model_to_use,
                    input_tokens=total_input_tokens, output_tokens=total_output_tokens,
                    cost_usd=total_cost, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    turns=turn, response_summary=text[:120],
                    escalation_reason=f"stop={response.stop_reason}",
                )
                self._run_interruptors(cortex)
                return text, total_cost

    def _extend_twm_on_positive_valence(self, response_text: str, cortex) -> None:
        """
        Signal B (Change 3 / D027): extend TTL of high-urgency TWM obs when
        response valence is positive (≥ 0.3).

        Lightweight valence check using keyword counting (same heuristic as
        prefrontal_cortex.assess_valence, but inline here to avoid import cycle).
        Only extends obs with urgency ≥ 0.7 that are still active (non-expired).
        """
        _POS = {"thanks", "great", "good", "helpful", "done", "solved", "worked",
                "yes", "correct", "perfect", "appreciate", "excellent", "success"}
        _NEG = {"error", "fail", "wrong", "not", "can't", "cannot", "sorry",
                "issue", "problem", "broken", "no", "unfortunately"}
        low = response_text.lower()
        pos = sum(1 for w in _POS if w in low)
        neg = sum(1 for w in _NEG if w in low)
        valence = (pos - neg) / max(1, pos + neg)

        if valence < 0.3:
            return  # Not positive enough to confirm relevance

        twm_obs = cortex.twm_read(limit=50, include_integrated=False)
        for obs in twm_obs:
            if obs.get("urgency", 0.2) >= 0.7:
                cortex.twm_extend_ttl(
                    obs["id"], reason=f"signal_B_valence={valence:.2f}"
                )

    def _run_interruptors(self, cortex):
        """Run all interruptors. Alerts are written to TWM and printed to console."""
        try:
            from ...cognition.interruptors import run_all
            alerts = run_all(cortex)
            for alert in alerts:
                console.print(f"[bold yellow][INTERRUPTOR] {alert}[/]")
        except Exception:
            pass  # Interruptors are advisory — never crash reasoning

    def _summarize_input(self, inp: dict) -> str:
        """Produce a short human-readable summary of tool input params."""
        if not inp:
            return ""
        parts = []
        for k, v in inp.items():
            vs = str(v)
            if len(vs) > 60:
                vs = vs[:57] + "..."
            parts.append(f"{k}={vs!r}")
        return ", ".join(parts)

    def _extract_text(self, response) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    def _estimate_cost(self, usage, model: str | None = None) -> float:
        m = model or self.model
        if "haiku" in m:
            # Haiku: ~$0.80/MTok input, $4/MTok output
            return (usage.input_tokens * 0.0000008) + (usage.output_tokens * 0.000004)
        # Sonnet 4.6: $3/MTok input, $15/MTok output
        return (usage.input_tokens * 0.000003) + (usage.output_tokens * 0.000015)
