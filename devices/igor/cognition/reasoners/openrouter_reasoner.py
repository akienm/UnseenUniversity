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
from .base import BaseReasoner
from ..system_prompt import build_system_prompt
from ..forensic_logger import log_reasoning_call, log_tool_call

console = Console()

DEFAULT_MODEL      = "anthropic/claude-sonnet-4-6"
OPENROUTER_BASE    = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# _build_session_context and _build_memory_context live in BaseReasoner (WO8)


def preparse_via_openrouter(
    user_input: str,
    habits: list,
    model: str = "openai/gpt-4o-mini",
) -> dict:
    """
    Run preparse classification via OpenRouter when Ollama is unavailable.
    Same prompt/output contract as preparse() in ollama_reasoner.py.
    Falls back to should_escalate=True on any error.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"intent": "general", "keywords": [], "habit_match": None,
                "confidence": 0.0, "should_escalate": True}

    habit_desc = ""
    if habits:
        habit_desc = "\n\nAvailable habits:\n" + "\n".join(
            f"- ID={h.id}: trigger='{h.metadata.get('trigger', '')}' desc='{h.narrative[:60]}'"
            for h in habits
        )

    prompt = f"""Classify this user input. Reply with ONLY a JSON object, no other text.

User input: "{user_input}"{habit_desc}

JSON fields:
- intent: one word from: greeting, meta_question, factual_question, action_request, memory_instruction, general
- keywords: array of 2-4 important words from the input
- habit_id: the habit ID string if a habit matches, or null
- confidence: number from 0.0 to 1.0 for how well a habit matches
- should_escalate: true if needs deep reasoning, false if simple

Example output:
{{"intent": "factual_question", "keywords": ["capital", "france"], "habit_id": null, "confidence": 0.0, "should_escalate": true}}"""

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 200,
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

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
        else:
            raise ValueError("No JSON in response")

        habit_match = None
        if parsed.get("habit_id") and habits:
            habit_match = next((h for h in habits if h.id == parsed["habit_id"]), None)

        return {
            "intent": parsed.get("intent", "general"),
            "keywords": parsed.get("keywords", []),
            "habit_match": habit_match,
            "confidence": float(parsed.get("confidence", 0.0)),
            "should_escalate": bool(parsed.get("should_escalate", True)),
        }
    except Exception as exc:
        console.print(f"[yellow][PREPARSE] OR preparse failed ({exc}), defaulting to escalate[/]")
        return {"intent": "general", "keywords": [], "habit_match": None,
                "confidence": 0.0, "should_escalate": True}


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
    ) -> tuple[str, float]:
        """Run full agentic tool loop via OpenRouter."""
        t0 = time.perf_counter()

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
                    t_tool = time.perf_counter()
                    result = registry.execute(tool_name, kwargs)
                    tool_elapsed = int((time.perf_counter() - t_tool) * 1000)
                    result_preview = str(result)[:120].replace("\n", " ")
                    console.print(f"[dim][OR turn={turn}]   → {result_preview}[/]")
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
