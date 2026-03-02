"""
Base reasoner interface.
All reasoning adapters implement this - Anthropic API, browser-based AIs, local models.
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
    ) -> tuple[str, float]:
        """
        Generate a response.
        Returns (response_text, cost_in_usd).
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

    # ── Shared context builders (WO8) ─────────────────────────────────────────

    def _build_session_context(self, cortex) -> str:
        """Recent ring memory as session context block. Empty string if nothing relevant."""
        if cortex is None:
            return ""
        all_entries = cortex.read_ring_memory(limit=50)
        filtered = [e for e in all_entries if e["category"] not in _RING_EXCLUDE]
        entries = filtered[-_RING_CONTEXT_LIMIT:]
        if not entries:
            return ""
        lines = ["\n\nRecent session context (newest last):"]
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
