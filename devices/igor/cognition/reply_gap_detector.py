"""
reply_gap_detector.py — T-any-thoughts-habit-failure (#468)

Detects "any thoughts?" and similar prompts as a signal that Igor
failed to reply to a prior turn. Backtracks through recent ring to
find the gap, flags it for sleep self-review.

The root problem: many interaction paths don't create reply-obligation
markers, so Igor goes silent and needs a human prod. This module
detects that prod and creates a learning signal.

## How it works

1. is_reply_prod(text) — True if the user is prodding for a reply
2. find_reply_gap(cortex) — scans recent ring for user_turn entries
   that weren't followed by an Igor reply within REPLY_GAP_WINDOW
3. flag_reply_gap(cortex, gap) — deposits an EPISODIC memory tagged
   reply_gap=True for sleep self-review to process

Inertia: LOW (new module, doesn't touch brainstem)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from .forensic_logger import log_error

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)

REPLY_PROD_PATTERNS = [
    r"\bany\s+thoughts\b",
    r"\bwhat\s+do\s+you\s+think\b",
    r"\byou\s+still\s+there\b",
    r"\bhello\?\s*$",
    r"\bigor\?\s*$",
    r"\bare\s+you\s+there\b",
    r"\bstill\s+thinking\b",
    r"\bwell\?\s*$",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in REPLY_PROD_PATTERNS]

REPLY_GAP_WINDOW = 10


@dataclass
class ReplyGap:
    """A user turn that didn't get a reply."""

    user_input: str
    ring_category: str
    timestamp: str
    turns_ago: int
    metadata: dict[str, Any] = field(default_factory=dict)


def is_reply_prod(text: str) -> bool:
    """True if the text is a prod for a missing reply."""
    if not text or len(text) > 200:
        return False
    return any(p.search(text) for p in _COMPILED)


def find_reply_gap(
    cortex: "Cortex", window: int = REPLY_GAP_WINDOW
) -> Optional[ReplyGap]:
    """Scan recent ring entries for a user turn not followed by an Igor reply.

    Walks backward through the last `window` ring entries. A gap is a
    user_turn entry with no habit_trace, tool_result, or system_info
    entry between it and the next user_turn (meaning Igor was silent).
    """
    try:
        entries = cortex.read_ring_memory(limit=window * 2)
    except Exception as exc:
        log_error(kind="REPLY_GAP", detail=f"read_ring failed: {exc}")
        return None

    if not entries:
        return None

    last_user_turn = None
    saw_igor_reply = False
    turns_since = 0

    for entry in entries:
        cat = ""
        content = ""
        ts = ""
        if isinstance(entry, dict):
            cat = entry.get("category", "")
            content = entry.get("content", "")
            ts = entry.get("created_at", "")
        elif hasattr(entry, "category"):
            cat = getattr(entry, "category", "")
            content = getattr(entry, "content", getattr(entry, "narrative", ""))
            ts = getattr(entry, "created_at", "")

        if cat == "user_turn":
            if last_user_turn is not None and not saw_igor_reply:
                user_text = (
                    last_user_turn.get("content", "")
                    if isinstance(last_user_turn, dict)
                    else getattr(last_user_turn, "content", "")
                )
                if user_text.startswith("USER_INPUT: "):
                    user_text = user_text[12:]
                return ReplyGap(
                    user_input=user_text[:500],
                    ring_category="user_turn",
                    timestamp=(
                        last_user_turn.get("created_at", "")
                        if isinstance(last_user_turn, dict)
                        else getattr(last_user_turn, "created_at", "")
                    ),
                    turns_ago=turns_since,
                )
            last_user_turn = entry
            saw_igor_reply = False
            turns_since = 0
        elif cat in ("habit_trace", "tool_result", "think_trace"):
            saw_igor_reply = True
        turns_since += 1

    return None


def flag_reply_gap(cortex: "Cortex", gap: ReplyGap) -> Optional[str]:
    """Deposit an EPISODIC memory flagging a reply gap for sleep review.

    Returns the memory ID if deposited, None on failure.
    """
    try:
        from ..memory.models import Memory, MemoryType

        mem = Memory(
            narrative=(
                f"REPLY_GAP: user said {gap.user_input[:200]!r} "
                f"but Igor did not reply. Detected {gap.turns_ago} turns later "
                f"via reply-prod signal. Needs sleep review to find root cause "
                f"(pe_chain blocked? missing habit? scope guard stall?)."
            ),
            memory_type=MemoryType.EPISODIC,
            metadata={
                "reply_gap": True,
                "user_input": gap.user_input[:500],
                "turns_ago": gap.turns_ago,
                "gap_timestamp": gap.timestamp,
                "flagged_at": datetime.now(timezone.utc).isoformat(),
                "needs_sleep_review": True,
            },
        )
        stored = cortex.store(mem)
        mem_id = stored.id if hasattr(stored, "id") else str(stored)
        logger.info("[REPLY_GAP] flagged: %s (turns_ago=%d)", mem_id, gap.turns_ago)
        return mem_id
    except Exception as exc:
        log_error(kind="REPLY_GAP", detail=f"flag deposit failed: {exc}")
        return None


def detect_and_flag(cortex: "Cortex", user_input: str) -> Optional[str]:
    """One-call convenience: if user_input is a reply prod, find and flag the gap.

    Returns memory ID of the flagged gap, or None if no gap found or not a prod.
    """
    if not is_reply_prod(user_input):
        return None

    gap = find_reply_gap(cortex)
    if gap is None:
        logger.debug("[REPLY_GAP] prod detected but no gap found in ring")
        return None

    try:
        cortex.twm_push(
            source="reply_gap_detector",
            content_csb=f"REPLY_GAP_DETECTED|{gap.user_input[:100]}|turns_ago={gap.turns_ago}",
            salience=0.7,
            urgency=0.5,
            ttl_seconds=300,
            category="reply_gap",
            metadata={"reply_gap": True, "turns_ago": gap.turns_ago},
        )
    except Exception as exc:
        log_error(kind="REPLY_GAP", detail=f"twm_push failed: {exc}")

    return flag_reply_gap(cortex, gap)
