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
import urllib.request
import urllib.error

from rich.console import Console

from ...memory.models import Memory
from ...tools.registry import registry
from ... import tools as _tools  # noqa: F401 — registers all tools
from .base import BaseReasoner
from ..system_prompt import build_system_prompt

console = Console()

DEFAULT_MODEL      = "anthropic/claude-sonnet-4-6"
OPENROUTER_BASE    = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# Ring context: same excluded categories as AnthropicReasoner (URGENT.1)
_RING_EXCLUDE = ("tool_trace", "judgment", "action_impulse", "ne_diagnostic")
RING_CONTEXT_LIMIT = 5


class OpenRouterReasoner(BaseReasoner):
    """Reason via any model accessible through OpenRouter's OpenAI-compatible API."""

    def __init__(self, model: str | None = None, show_model_tag: bool = True):
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
    ) -> tuple[str, float]:
        """Run full agentic tool loop via OpenRouter."""
        # WO1: dynamic system prompt from cortex memories
        system = build_system_prompt(cortex, instance_id)

        content = user_input
        session_ctx = self._build_session_context(cortex)
        mem_ctx = self._build_memory_context(relevant_memories)
        if session_ctx:
            content += session_ctx
        if mem_ctx:
            content += mem_ctx

        messages = [{"role": "user", "content": content}]
        tools = registry.to_openai_schemas()
        total_cost = 0.0
        turn = 0

        while True:
            turn += 1
            response = self._call_api(messages, tools, system=system)
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
                return text, total_cost

            elif finish_reason == "tool_calls" or msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", [])
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        kwargs = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        kwargs = {}

                    input_summary = ", ".join(
                        f"{k}={str(v)[:40]!r}" for k, v in kwargs.items()
                    )
                    console.print(f"[dim][OR turn={turn}] ⚙ {tool_name}({input_summary})[/]")
                    result = registry.execute(tool_name, kwargs)
                    result_preview = str(result)[:120].replace("\n", " ")
                    console.print(f"[dim][OR turn={turn}]   → {result_preview}[/]")

                    if cortex is not None:
                        cortex.write_ring(
                            f"TOOL:{tool_name} input={input_summary} result={result_preview}",
                            category="tool_trace",
                        )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    })

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

    def _build_session_context(self, cortex) -> str:
        if cortex is None:
            return ""
        entries = cortex.read_ring_memory(limit=50)
        filtered = [e for e in entries if e["category"] not in _RING_EXCLUDE]
        entries = filtered[-RING_CONTEXT_LIMIT:]
        if not entries:
            return ""
        lines = ["\n\nRecent session context (newest last):"]
        for e in entries:
            ts = e["timestamp"][11:16] if len(e["timestamp"]) >= 16 else e["timestamp"]
            lines.append(f"[{ts}] {e['content']}")
        return "\n".join(lines)

    def _build_memory_context(self, memories: list[Memory]) -> str:
        if not memories:
            return ""
        high_rel = [m for m in memories if getattr(m, "relevance_score", 0.0) >= 0.5][:3]
        if not high_rel:
            high_rel = sorted(
                memories[:5],
                key=lambda m: getattr(m, "relevance_score", 0.0),
                reverse=True,
            )[:2]
        if not high_rel:
            return ""
        lines = ["\n\nRelevant memories:"]
        for m in high_rel:
            lines.append(f"- [{m.memory_type.value}] {m.narrative}")
        return "\n".join(lines)

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
        if "mistral" in m or "mixtral" in m:
            return inp * 0.0000002 + out * 0.0000006
        # Generic estimate for unknown models
        return inp * 0.000001 + out * 0.000002
