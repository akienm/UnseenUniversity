"""
thread_anchor.py — T-thread-to-fallthrough

Compact per-turn anchors that survive context window trimming.

write_thread_anchor(): called post-turn from main.py (after ring write).
  Stores an EPISODIC memory tagged metadata.thread_anchor=true so it is
  queryable by recency without semantic search.

read_thread_anchor(): called from base._build_session_context() to prepend
  a continuity header before the LLM context assembly.

Design note (per Akien 2026-04-04):
  Context assembly owns "what enters the context window" — this belongs in
  base.py, not in a habit. Habits own behavior dispatch, not context
  construction. The functional boundary is clean: base.py reads the anchor,
  thalamus/main.py writes it.
"""

import logging

from ..memory.models import Memory, MemoryType
from .registry import Tool, registry

logger = logging.getLogger(__name__)


# ── Write ─────────────────────────────────────────────────────────────────────


def write_thread_anchor(
    cortex,
    user_input: str,
    response_text: str,
    intent: str,
    turn_n: int = 0,
) -> None:
    """
    Write a compact thread anchor immediately after each turn's ring write.

    Swallows all exceptions — must never crash Igor.
    Called by main.py; not exposed to users directly.
    """
    try:
        u_snippet = user_input[:160].replace("\n", " ").strip()
        r_snippet = response_text[:160].replace("\n", " ").strip()
        narrative = (
            f"[Thread turn {turn_n}] "
            f"User: {u_snippet} | Igor: {r_snippet} | intent={intent}"
        )
        mem = Memory(
            narrative=narrative,
            memory_type=MemoryType.EPISODIC,
            metadata={
                "thread_anchor": True,
                "turn_n": turn_n,
                "intent": intent,
                "source": "thread_anchor",
            },
        )
        cortex.store(mem)
    except Exception as exc:
        logger.warning("thread_anchor write failed (turn %s): %s", turn_n, exc)


# ── Read ──────────────────────────────────────────────────────────────────────


def read_thread_anchor(cortex, limit: int = 3) -> str:
    """
    Query the N most recent thread anchors and format as a compact context header.

    Returns empty string if no anchors exist or on any error.
    Used by base._build_session_context() to prepend orientation context
    before task_sets / TWM urgents / ring entries — so a post-trim Igor
    still knows what conversation it's in.
    """
    try:
        # Direct SQL query by metadata key — ordered by recency, not relevance.
        # Most recent anchor is always what we need for continuity.
        # Uses jsonb_exists() per db_proxy convention (never metadata ? 'key').
        sql = (
            "SELECT narrative, timestamp FROM memories "
            "WHERE memory_type = %s AND jsonb_exists(metadata, 'thread_anchor') "
            "ORDER BY timestamp DESC LIMIT %s"
        )
        rows: list[tuple[str, str]] = []
        with cortex._db() as conn:
            conn.execute(sql, [MemoryType.EPISODIC.value, limit])
            rows = conn.fetchall()

        if not rows:
            return ""

        # Reverse so oldest anchor is first (chronological order for context)
        rows = list(reversed(rows))
        lines = ["[Thread anchors — prior turns:]"]
        for narrative, ts in rows:
            ts_short = ts[11:16] if len(ts) >= 16 else ts  # HH:MM
            lines.append(f"  [{ts_short}] {narrative[:220]}")
        return "\n".join(lines)

    except Exception as exc:
        logger.warning("thread_anchor read failed: %s", exc)
        return ""


# ── Tool registrations ────────────────────────────────────────────────────────
# These allow Igor to read/write anchors explicitly if a habit or schema needs it.
# The primary path (main.py write + base.py read) bypasses tool dispatch.


def _tool_read_thread_anchor(**_) -> str:
    """Read the most recent thread anchors. No-arg tool for habit use."""
    # cortex is not available in the tool context; used via main.py/base.py paths only.
    return "thread_anchor: call read_thread_anchor(cortex) directly — not available as no-arg tool"


registry.register(
    Tool(
        name="read_thread_anchor",
        description="Read recent thread anchors for conversation continuity context.",
        parameters={},
        fn=_tool_read_thread_anchor,
    )
)
