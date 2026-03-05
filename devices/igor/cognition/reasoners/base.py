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
  KoboldCppReasoner(LocalReasoner)  — local KoboldCpp

Eventually: no upstream at all. Pure habit execution replaces reasoning entirely.
"""

from abc import ABC, abstractmethod
from ...memory.models import Memory

# Shared ring-context constants (WO8: single source of truth — was duplicated
# verbatim in anthropic.py and openrouter_reasoner.py)
_RING_EXCLUDE = frozenset({"tool_trace", "judgment", "action_impulse", "ne_diagnostic"})
_RING_CONTEXT_LIMIT = 5

# ── Token economy (shared across all reasoners) ────────────────────────────────
# Each tool result is capped before it enters the message history.
# This prevents a single large command output (find, cat big file, etc.) from
# blowing up the context window.  Big tasks should be decomposed, not ingested
# in one shot.
TOOL_RESULT_MAX_CHARS = 20_000   # ~5 K tokens — generous for real data
MAX_TURNS = 25                   # hard limit on tool-call rounds per session
CONTEXT_WARN_CHARS = 100_000     # ~25 K tokens total messages — emit a warning
CONTEXT_HARD_CAP_CHARS = 150_000 # hard trim threshold — drop oldest tool results above this


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

    def _build_session_context(self, cortex) -> str:
        """
        Recent ring memory as session context block. Empty string if nothing relevant.

        Change 4 (D028): high-urgency TWM obs (urgency ≥ 0.7) are flagged distinctly
        at the top of the context injection so the model notices them first.
        """
        if cortex is None:
            return ""
        all_entries = cortex.read_ring_memory(limit=50)
        filtered = [e for e in all_entries if e["category"] not in _RING_EXCLUDE]
        entries = filtered[-_RING_CONTEXT_LIMIT:]

        lines = []

        # ── Change 4: inject high-urgency TWM observations first ──────────────
        try:
            twm_obs = cortex.twm_read(limit=15, include_integrated=False)
            urgent = [
                o for o in twm_obs
                if o.get("urgency", 0.2) >= 0.7
                and o.get("source") not in ("narrative_engine", "ne_loop_guard")
            ]
            if urgent:
                lines.append("\n\n⚠ URGENT observations (act on these):")
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
