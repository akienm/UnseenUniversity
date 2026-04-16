"""
receive_cc_direction.py — Hold strategic direction received from Claude Code.

When a long CC message arrives that isn't a tool call, this tool:
  1. Deposits a FACTUAL memory node with identity_weight=0.9 (high salience/identity tie)
  2. Pushes the direction summary into TWM with 6-hour TTL and high salience
  3. Posts an acknowledgment to the CC channel

This closes the matrix gap where Igor receives D316/D317-style strategic direction
but has no pattern to hold it across turns.

Called by PROC_RECEIVE_CC_DIRECTION habit (code_ref) when trigger words fire.
Single-arg for habit auto-dispatch compatibility.
"""

from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

_DIRECTION_TTL_SECONDS = 6 * 3600  # 6 hours


def _get_cortex():
    from ..memory.cortex import Cortex as _Cortex

    return _Cortex(None)


def receive_cc_direction(content: str) -> str:
    """
    Absorb strategic direction from a CC message.

    content: the full CC message text (single-arg for habit auto-dispatch)

    Returns a confirmation string with the deposited memory ID.
    """
    if not content or not content.strip():
        return "[receive_cc_direction] empty content — skipped"

    content = content.strip()
    # Strip leading CC: prefix if present (double-prefix guard)
    if content.startswith("CC:"):
        content = content[3:].strip()
    if not content:
        return "[receive_cc_direction] empty after stripping CC: prefix — skipped"

    try:
        from ..memory.models import Memory, MemoryType

        cortex = _get_cortex()

        # ── 1. Deposit FACTUAL node ───────────────────────────────────────────
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        summary = content[:400]  # cap narrative at 400 chars
        mem = Memory(
            narrative=f"[CC DIRECTION {ts}] {summary}",
            memory_type=MemoryType.FACTUAL,
            source="claude-code",
            context_of_encoding="strategic direction from Claude Code session",
            valence=0.3,
            arousal=0.4,
            metadata={
                "identity_weight": 0.9,
                "category": "strategic_direction",
                "source": "claude-code",
                "direction_ts": ts,
            },
        )
        cortex.store(mem)
        mem_id = mem.id
        log.info(
            "[receive_cc_direction] deposited FACTUAL %s: %s…",
            mem_id,
            summary[:80],
        )

        # ── 2. Push to TWM with 6-hour TTL ───────────────────────────────────
        twm_content = f"[STRATEGIC DIRECTION from CC] {summary[:300]}"
        cortex.twm_push(
            source="receive_cc_direction",
            content_csb=twm_content,
            salience=0.9,
            urgency=0.6,
            ttl_seconds=_DIRECTION_TTL_SECONDS,
            category="strategic_direction",
            metadata={"mem_id": mem_id, "identity_weight": 0.9},
        )

        # ── 3. Post acknowledgment to CC channel ─────────────────────────────
        try:
            from .channel_post import post_to_channel

            preview = content[:80] + ("…" if len(content) > 80 else "")
            post_to_channel(
                f"[DIRECTION RECEIVED] Stored as {mem_id}. Context held for 6h: {preview}"
            )
        except Exception as _ch_e:
            log.warning("[receive_cc_direction] channel post failed: %s", _ch_e)

        return (
            f"[receive_cc_direction] stored {mem_id} + TWM injection: {summary[:60]}…"
        )

    except Exception as e:
        log.error("[receive_cc_direction] error: %s", e, exc_info=True)
        return f"[receive_cc_direction ERROR] {e}"


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from ..tools.registry import registry, Tool

    registry.register(
        Tool(
            name="receive_cc_direction",
            description=(
                "Absorb strategic direction from a long Claude Code message. "
                "Deposits a FACTUAL memory node (identity_weight=0.9), injects into TWM "
                "with 6-hour TTL, and posts channel acknowledgment. "
                "Called automatically by PROC_RECEIVE_CC_DIRECTION when direction trigger words fire."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The CC message text to absorb as strategic direction",
                    }
                },
                "required": ["content"],
            },
            fn=receive_cc_direction,
        )
    )
except Exception as _exc:
    from ..cognition.forensic_logger import log_error as _le
    _le(kind="SILENT_EXCEPT", detail=f"receive_cc_direction.py:139: {_exc}")
