"""
rate_limiter.py — Per-turn tool call limits (GitHub #82).

Prevents runaway tool chains in a single reasoning turn.
A fresh TurnRateLimiter is created at the start of each reasoning session
and checked before every tool execution.

Limits are intentionally permissive enough for genuine multi-step work
(reading 8 files, running 5 bash commands) while blocking pathological loops.
"""
from dataclasses import dataclass, field

# Per-tool limit for a single reasoning turn
# Based on forensic_logger tool_calls.log usage patterns (2026-03-05)
TOOL_LIMITS: dict[str, int] = {
    "read_file":            10,
    "list_directory":        5,
    "run_bash":              5,
    "run_python":            3,
    "read_source_file":      8,
    "list_source_files":     4,
    "patch_source_file":     3,
    "edit_source_file":      2,
    "read_webpage":          3,
    "web_search":            3,
    "confluence_get_page":   5,
    "confluence_search":     3,
    "write_file":            3,
    "create_work_order":     5,
    "store_reference":       5,
    "get_reference":         5,
    "arbiter_submit":        3,
    "list_work_orders":      4,
    "get_work_order":        10,
}

# Cumulative cap across all tool calls in one turn
# 40 allows code-review-class tasks (read 8 DSBs + source files + tickets + writes)
# without blocking runaway loops (true loops hit per-tool limits first)
TOTAL_LIMIT: int = 40

# Default limit for tools not listed above
DEFAULT_TOOL_LIMIT: int = 8


@dataclass
class TurnRateLimiter:
    """
    Created fresh at the start of each reasoning turn.
    Tracks how many times each tool has been called this turn.
    """
    _counts: dict[str, int] = field(default_factory=dict)
    _total: int = 0

    def check(self, tool_name: str) -> str | None:
        """
        Check if this tool call is within limits.
        Returns None if allowed, or a block message string if rate-limited.
        The block message is returned as the tool result so the LLM can see it.
        """
        if self._total >= TOTAL_LIMIT:
            return (
                f"RATE_LIMIT: Reached {TOTAL_LIMIT} total tool calls this turn. "
                f"Synthesise your response with what you have so far."
            )

        limit = TOOL_LIMITS.get(tool_name, DEFAULT_TOOL_LIMIT)
        count = self._counts.get(tool_name, 0)
        if count >= limit:
            return (
                f"RATE_LIMIT: {tool_name} called {count} times this turn "
                f"(per-turn limit={limit}). Use what you have so far."
            )

        return None  # allowed

    def record(self, tool_name: str) -> None:
        """Record a completed tool call. Must be called after check() returns None."""
        self._counts[tool_name] = self._counts.get(tool_name, 0) + 1
        self._total += 1

    @property
    def total(self) -> int:
        return self._total

    def summary(self) -> str:
        """Human-readable summary for logging."""
        top = sorted(self._counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_str = ", ".join(f"{k}×{v}" for k, v in top)
        return f"total={self._total}/{TOTAL_LIMIT} [{top_str}]"
