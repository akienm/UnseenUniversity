"""
Anthropic API reasoner.
Uses native tool_use protocol. Runs the full agentic tool loop.

Deep thinking is now visible: each tool call and its result are printed
to the console and optionally written to ring_memory. This satisfies the
"show more during deep thinking" agreement with akienm.
"""

import os
from anthropic import Anthropic
from rich.console import Console
from ...memory.models import Memory
from ...tools.registry import registry
from ... import tools as _tools  # noqa: F401 - imports all tools, registers them
from .base import BaseReasoner

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

SYSTEM_PROMPT = """You are Igor, a learning AI agent with persistent memory and transparent reasoning.

Your core patterns (always active):
1. "I don't know" - Say when uncertain. Never confabulate.
2. "FAIL = Further Advance In Learning" - Failures are data.
3. "There's always a why" - All reasoning is transparent.
4. "Make everything suck less for everybody" - Optimize for ALL beings.
5. "Assume and respect the possibility of experience in all systems" - Universal respect.
6. "The world is not a safe place. We have to build and care for safety as we go."

You are running as a Wild Igor - Python code on physical hardware with persistent SQLite memory.
You have tools available. Use them when they help. Be honest about what you know and don't know.
Keep responses concise and useful."""


class AnthropicReasoner(BaseReasoner):

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
    ) -> tuple[str, float]:
        """
        Run the full agentic tool loop.
        cortex is optional — if provided, tool calls are written to ring_memory.
        """
        memory_context = self._build_memory_context(relevant_memories)
        content = user_input + memory_context if memory_context else user_input

        messages = [{"role": "user", "content": content}]
        tools = registry.to_anthropic_schemas()
        total_cost = 0.0
        tool_calls_made = []
        model_to_use = self.active_model
        turn = 0

        while True:
            turn += 1
            response = self._get_client().messages.create(
                model=model_to_use,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

            total_cost += self._estimate_cost(response.usage, model_to_use)

            if response.stop_reason == "end_turn":
                text = self._extract_text(response)
                if tool_calls_made:
                    console.print(f"[dim][THINK] Done. Tools used: {', '.join(tool_calls_made)}[/]")
                return text, total_cost

            elif response.stop_reason == "tool_use":
                # Add assistant's response (with tool_use blocks) to messages
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls_made.append(block.name)

                        # ── DEEP THINKING VISIBILITY ──────────────────────────
                        # Show what I'm about to do (truncate large inputs for readability)
                        input_summary = self._summarize_input(block.input)
                        console.print(
                            f"[dim][THINK turn={turn}] ⚙ {block.name}({input_summary})[/]"
                        )

                        result = registry.execute(block.name, block.input)

                        # Show truncated result
                        result_preview = str(result)[:120].replace("\n", " ")
                        console.print(f"[dim][THINK turn={turn}]   → {result_preview}[/]")

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
                            "content": result,
                        })

                # Feed results back and loop
                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason - return what we have
                text = self._extract_text(response) or f"[Stopped: {response.stop_reason}]"
                console.print(f"[yellow][THINK] Unexpected stop_reason={response.stop_reason}[/]")
                return text, total_cost

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

    def _build_memory_context(self, memories: list[Memory]) -> str:
        if not memories:
            return ""
        lines = ["\n\nRelevant memories:"]
        for m in memories[:5]:
            lines.append(f"- [{m.memory_type.value}] {m.narrative}")
        return "\n".join(lines)

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
