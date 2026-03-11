"""
Base reasoner interface — two-level hierarchy (Change 2 / D026).

Level 1 — transport base classes:
  BaseReasoner (abstract)
  ├── LocalReasoner(BaseReasoner)   — no cost, latency variance, no tools
  ├── APIReasoner(BaseReasoner)     — budget tracking, rate limits, reliable tools
  └── BrowserReasoner(BaseReasoner) — zero cost, session-fragile, no tools (DECLARED ONLY)

Level 2 — model family classes:
  ModelFamily(BaseReasoner)           — groups by model identity; handles failover
  └── ClaudeFamily(ModelFamily)

Concrete implementations:
  AnthropicReasoner(APIReasoner)    — Anthropic API
  OpenRouterReasoner(APIReasoner)   — OpenRouter API
  OllamaReasoner(LocalReasoner)     — local Ollama
  KoboldCppReasoner(LocalReasoner)  — local KoboldCpp (batch only; interactive uses OllamaReasoner)

Eventually: no cloud inference at all. Pure habit execution replaces reasoning entirely.
"""

import json
import os
import threading
import urllib.request
from abc import ABC, abstractmethod
from ...memory.models import Memory

# ── Global exit signal ─────────────────────────────────────────────────────────
# Set by main._stdin_reader when /exit or /quit is typed.
# Checked at the top of each agentic turn so a long API call stops at the
# next turn boundary instead of running to completion.
exit_requested: threading.Event = threading.Event()

# Shared ring-context constants (WO8: single source of truth — was duplicated
# verbatim in anthropic.py and openrouter_reasoner.py)
_RING_EXCLUDE = frozenset({"tool_trace", "judgment", "action_impulse", "ne_diagnostic"})
_RING_CONTEXT_LIMIT = 10

# ── Token economy (shared across all reasoners) ────────────────────────────────
# Each tool result is capped before it enters the message history.
# This prevents a single large command output (find, cat big file, etc.) from
# blowing up the context window.  Big tasks should be decomposed, not ingested
# in one shot.
TOOL_RESULT_MAX_CHARS = 8_000    # ~2 K tokens — enough for real data; trim forces decomposition
MAX_TURNS = int(os.getenv("IGOR_MAX_TURNS", "8"))  # env-overridable; default 8 prevents runaway agentic burns
CONTEXT_WARN_CHARS = 80_000      # ~20 K tokens — warn earlier, prompt breaking into steps
CONTEXT_HARD_CAP_CHARS = 120_000 # hard trim — drop oldest tool results above this

# ── Cost guardrails (shared across all API reasoners) ─────────────────────────
# IGOR_CALL_COST_WARN_USD: stop the agentic loop if a single call exceeds this.
# IGOR_RESEARCH_MODE: set true to allow bulk reads (confluence, source files, web).
# IGOR_RESEARCH_TOOL_CAP: max big-read tool calls per reasoning session when not in research mode.
CALL_COST_WARN_USD  = float(os.getenv("IGOR_CALL_COST_WARN_USD", "0.30"))
RESEARCH_TOOL_CAP   = int(os.getenv("IGOR_RESEARCH_TOOL_CAP", "5"))
RESEARCH_MODE       = os.getenv("IGOR_RESEARCH_MODE", "false").lower() in ("1", "true", "yes")

# Tools that constitute expensive external reads — capped when not in research mode.
# Local file reads (read_source_file, list_source_files) are free and NOT capped.
# Only external API calls that cost money or tokens are gated.
BIG_READ_TOOLS = frozenset({
    "confluence_search", "confluence_get_page",
    "web_search",
})

# Bash command prefixes that indicate external/expensive operations via run_bash.
# Plain file reads via bash are NOT counted — only network/search patterns.
BASH_READ_PATTERNS = ("curl ", "wget ")


class BaseReasoner(ABC):
    """
    A reasoning adapter translates Igor's internal state into whatever
    protocol a specific AI speaks, executes the conversation, handles
    tool calls, and returns a plain text response.

    Igor doesn't care which reasoner is active. It calls reason() and
    gets text back.
    """

    @abstractmethod
    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        preparse_csb: str = "",
    ) -> tuple[str, float]:
        """
        Generate a response.
        Returns (response_text, cost_in_usd).
        preparse_csb: structured PARSED_INPUT block injected into context for cloud reasoners.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this reasoner."""
        ...

    # ── Token economy ─────────────────────────────────────────────────────────

    @staticmethod
    def _cap_tool_result(result: str) -> str:
        """
        Truncate a tool result to TOOL_RESULT_MAX_CHARS.

        Appends a visible truncation notice so the model knows output was cut.
        Big outputs (find trees, large files) must be broken into smaller steps,
        not ingested whole — this cap enforces that discipline.
        """
        if len(result) <= TOOL_RESULT_MAX_CHARS:
            return result
        dropped = len(result) - TOOL_RESULT_MAX_CHARS
        return (
            result[:TOOL_RESULT_MAX_CHARS]
            + f"\n[TRUNCATED — {dropped} more chars not shown. "
            f"Break large tasks into smaller steps rather than reading everything at once.]"
        )

    @staticmethod
    def _messages_total_chars(messages: list) -> int:
        """Rough char count of all message content — used for context size warnings."""
        total = 0
        for m in messages:
            c = m.get("content") or ""
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict):
                        total += len(str(block.get("text", "") or block.get("content", "")))
                    else:
                        total += len(str(block))
        return total

    @staticmethod
    def _trim_messages(messages: list) -> list:
        """
        Hard context cap (#26): if total message chars exceed CONTEXT_HARD_CAP_CHARS,
        drop middle messages (oldest tool results) while preserving:
          - messages[0]: the initial user message with all injected context
          - messages[-4:]: the 4 most recent messages (current tool round)

        Inserts a visible placeholder so the model knows history was trimmed.
        Returns the trimmed list (or the original if under cap).
        """
        if len(messages) <= 3:
            return messages

        total = BaseReasoner._messages_total_chars(messages)
        if total <= CONTEXT_HARD_CAP_CHARS:
            return messages

        # Keep first (initial context) + last 4 (most recent reasoning)
        keep_tail = min(4, len(messages) - 1)
        dropped = messages[1 : len(messages) - keep_tail]
        dropped_chars = BaseReasoner._messages_total_chars(dropped)
        trimmed = (
            [messages[0]]
            + [{"role": "user", "content":
                f"[CONTEXT TRIMMED: {len(dropped)} older messages ({dropped_chars // 1000}K chars) "
                f"dropped to stay within context limit. Ask me to recap if needed.]"}]
            + messages[len(messages) - keep_tail :]
        )
        return trimmed

    # ── Shared tool-call display (#34) ────────────────────────────────────────

    @staticmethod
    def print_tool_call(tag: str, turn: int, name: str, args_summary: str, result_preview: str):
        """
        Uniform tool-call display across all reasoners.
        tag: short reasoner label, e.g. "THINK" or "OR"
        """
        from rich.console import Console as _Console
        _c = _Console()
        _c.print(f"[dim][{tag} turn={turn}] ⚙ {name}({args_summary})[/]")
        _c.print(f"[dim][{tag} turn={turn}]   → {result_preview}[/]")

    # ── Shared context builders (WO8) ─────────────────────────────────────────

    def _build_session_context(self, cortex, thread_id: str | None = None) -> str:
        """
        Recent ring memory as session context block. Empty string if nothing relevant.

        Change 4 (D028): high-urgency TWM obs (urgency ≥ 0.7) are flagged distinctly
        at the top of the context injection so the model notices them first.

        #158: TASK_SET entries for this thread go first — before ring, before all else.
        They represent the active goal of this attention nexus and must outcompete
        ambient ring content (the SSH/Hamlet problem).
        """
        if cortex is None:
            return ""
        all_entries = cortex.read_ring_memory(limit=50, thread_id=thread_id)
        filtered = [e for e in all_entries if e["category"] not in _RING_EXCLUDE]
        entries = filtered[-_RING_CONTEXT_LIMIT:]

        lines = []

        # ── #158: TASK_SET first — active goal anchors all context ────────────
        try:
            task_sets = cortex.twm_read(
                limit=3, include_integrated=False,
                thread_id=thread_id, category="task_set"
            )
            if task_sets:
                lines.append("🎯 ACTIVE TASK (complete this before anything else):")
                for t in task_sets:
                    goal = t["content_csb"].replace("TASK_SET|", "").strip()
                    lines.append(f"  → {goal[:200]}")
        except Exception:
            pass

        # ── Change 4: inject high-urgency TWM observations ────────────────────
        try:
            twm_obs = cortex.twm_read(
                limit=15, include_integrated=False, thread_id=thread_id
            )
            urgent = [
                o for o in twm_obs
                if o.get("urgency", 0.2) >= 0.7
                and o.get("source") not in ("narrative_engine", "ne_loop_guard")
                and o.get("category") != "task_set"  # already shown above
            ]
            if urgent:
                lines.append("\n⚠ URGENT observations (act on these):")
                for o in sorted(urgent, key=lambda x: x.get("urgency", 0.2) * x.get("salience", 0.5), reverse=True)[:5]:
                    urg = o.get("urgency", 0.2)
                    lines.append(f"  [urgency={urg:.1f}] {o['content_csb'][:150]}")
        except Exception:
            pass  # Never block context building

        if not entries:
            return "\n".join(lines) if lines else ""

        lines.append("\n\nRecent session context (newest last):")
        for e in entries:
            ts = e["timestamp"][11:16] if len(e["timestamp"]) >= 16 else e["timestamp"]
            lines.append(f"[{ts}] {e['content']}")
        return "\n".join(lines)

    def _build_memory_context(self, memories: list[Memory]) -> str:
        """Top relevant memories as a context block. Empty string if none qualify."""
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


    def _winnow_context(self, user_input: str, cortex, word_graph=None) -> list[Memory]:
        """
        Pre-call context filter — the breadcrumb step.

        Before the main reasoning call, ask a cheap model:
        "Given what we've been talking about and this new input,
        what specific memories do you need?"

        Returns targeted Memory objects to merge into relevant_memories.
        Skipped if: input is short/command, IGOR_CONTEXT_WINNOW=false, no OR key.

        This is the winnowing loop: smaller calls more often, converging on
        the relevant context rather than dumping everything every time.
        """
        # Skip for trivial inputs
        if len(user_input.strip()) < 20 or user_input.strip().startswith("/"):
            return []
        if os.getenv("IGOR_CONTEXT_WINNOW", "true").lower() in ("false", "0", "no"):
            return []
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key or cortex is None:
            return []

        # ── Build compact breadcrumb trail from ring ───────────────────────────
        try:
            ring = cortex.read_ring_memory(limit=10)
            filtered = [e for e in ring if e["category"] not in _RING_EXCLUDE]
            breadcrumbs = "\n".join(
                f"[{e['timestamp'][11:16]}] {e['content'][:80]}"
                for e in filtered[-5:]
            )
        except Exception:
            breadcrumbs = ""

        # ── Word graph hints: concepts activated by this input ─────────────────
        wg_hints = ""
        if word_graph is not None:
            try:
                predicted = word_graph.predict_next(user_input, n=5)
                if predicted:
                    wg_hints = "Activated concepts: " + ", ".join(w for w, _ in predicted)
            except Exception:
                pass

        prompt = (
            f"Context trail:\n{breadcrumbs}\n\n"
            f"{wg_hints}\n\n"
            f"New input: {user_input[:200]}\n\n"
            "List 2-3 specific memory search queries (comma-separated, 2-4 words each) "
            "to retrieve the most relevant context for responding. Be specific. No explanation."
        )

        # ── Cheap model call ───────────────────────────────────────────────────
        try:
            model = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 60,
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
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            queries_text = data["choices"][0]["message"]["content"].strip()
            queries = [q.strip() for q in queries_text.replace("\n", ",").split(",") if q.strip()][:3]
        except Exception:
            return []

        # ── Fetch memories for each query, dedupe ─────────────────────────────
        results: list[Memory] = []
        seen_ids: set[str] = set()
        for q in queries:
            try:
                found = cortex.search(q, limit=2)
                for m in found:
                    if m.id not in seen_ids:
                        seen_ids.add(m.id)
                        results.append(m)
            except Exception:
                pass
        return results


# ── Level 1 — Transport base classes (Change 2 / D026) ────────────────────────

class LocalReasoner(BaseReasoner):
    """
    Base for all local-hardware reasoners (Ollama, KoboldCpp).
    No API cost. Latency varies with hardware. No tool support.
    """
    supports_tools: bool = False
    response_format: str = "unstructured"
    cost_model: str = "free"
    reliability: str = "medium"
    supports_context_param: bool = False


class APIReasoner(BaseReasoner):
    """
    Base for all cloud API reasoners (Anthropic, OpenRouter).
    Has budget tracking, rate limits, and reliable tool support.
    Subclasses are expected to call record_spend() and check_before_call().
    """
    supports_tools: bool = True
    response_format: str = "structured"
    cost_model: str = "per_token"
    reliability: str = "high"
    supports_context_param: bool = False


class BrowserReasoner(BaseReasoner):
    """
    Placeholder for future browser-session AI access.
    Zero cost. Session-fragile. No tools. NOT IMPLEMENTED.
    Declared here to reserve the interface and document the capability model.
    """
    supports_tools: bool = False
    response_format: str = "unstructured"
    cost_model: str = "free"
    reliability: str = "low"
    supports_context_param: bool = False

    def reason(self, user_input, relevant_memories, core_patterns, instance_id,
               cortex=None):
        raise NotImplementedError("BrowserReasoner is not yet implemented.")

    def name(self) -> str:
        return "BrowserReasoner(not_implemented)"


# ── Level 2 — Model family classes (Change 2 / D026) ─────────────────────────

class ModelFamily(BaseReasoner):
    """
    Groups multiple channels by model identity. Handles failover across channels.
    Tries channels in order; moves to next on budget exhaustion or unavailability.
    Logs which channel was used and why fallback triggered.
    """

    channels: list[BaseReasoner] = []

    def reason(
        self,
        user_input: str,
        relevant_memories: list,
        core_patterns: list,
        instance_id: str,
        cortex=None,
    ) -> tuple[str, float]:
        last_exc = None
        for channel in self.channels:
            try:
                return channel.reason(
                    user_input, relevant_memories, core_patterns, instance_id,
                    cortex=cortex
                )
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(
            f"{self.name()} all channels failed. Last: {last_exc}"
        )

    def name(self) -> str:
        return f"ModelFamily({', '.join(c.name() for c in self.channels)})"


class ClaudeFamily(ModelFamily):
    """
    Claude model family across all available channels:
      1. AnthropicReasoner (direct API — fastest, most reliable)
      2. OpenRouterReasoner pointing at claude-sonnet (OR budget)
      3. BrowserReasoner (declared only; not yet implemented)

    Channels are populated at runtime from available credentials.
    Per-channel spend is tracked independently; combined Claude spend is the sum.
    """

    def name(self) -> str:
        if self.channels:
            return f"ClaudeFamily({', '.join(c.name() for c in self.channels)})"
        return "ClaudeFamily(no_channels)"
