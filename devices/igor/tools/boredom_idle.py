"""
boredom_idle.py — D272 Boredom idle loop.

When milieu is "too settled" (low arousal, near-neutral valence), Igor
traverses the EF questions tree and topics tree to find something interesting
to surface. Posts "[Igor wonders]" output to channel.

Design:
  - Check milieu state: arousal < BOREDOM_AROUSAL_THRESHOLD → idle
  - If idle: traverse EF_FACIA → random EF question → cortex.search
    for related nodes → pick one → post "[Igor wonders]"
  - On alternate cycles: traverse TOPICS_FACIA → highest arousal_weight
    topic → cortex.search → post "[Igor wonders about <topic>]"
  - Rate-limited: one post per COOLDOWN_SECONDS (default 900 = 15min)
  - If no good content found: post "[Igor notices] nothing in particular"
    at half the normal rate (tombstone so we don't spin silently)

Called by PROC_BOREDOM_TRIGGER (cognitive habit, schedule=10min).
Forensic log: ~/.TheIgors/logs/boredom_idle.log
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry

log = logging.getLogger(__name__)
from ..paths import paths
from .channel_post import post_to_channel as _post_to_channel

_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)

# Thresholds
_BOREDOM_AROUSAL_THRESHOLD = float(os.getenv("IGOR_BOREDOM_AROUSAL_THRESHOLD", "0.08"))
_BOREDOM_VALENCE_THRESHOLD = float(os.getenv("IGOR_BOREDOM_VALENCE_THRESHOLD", "0.15"))
_COOLDOWN_SECONDS = int(os.getenv("IGOR_BOREDOM_COOLDOWN_SECONDS", "900"))  # 15 min

# In-process state
_last_posted: float = 0.0
_cycle_counter: int = 0  # alternates EF vs topic traversal

_EF_NODE_IDS = ["EF_Q1", "EF_Q2", "EF_Q3", "EF_Q4"]
_TOPIC_NODE_IDS = [
    "TOPIC_LANGUAGE",
    "TOPIC_NEURO",
    "TOPIC_PROGRAMMING",
    "TOPIC_IGORS_DESIGN",
    "TOPIC_AI",
    "TOPIC_CLAUDE_CODE",
    "TOPIC_BIOLOGY",
    "TOPIC_PSYCHOLOGY",
    "TOPIC_CULTURE",
]




def _is_bored() -> tuple[bool, str]:
    """
    Returns (is_bored, reason_str).
    Too settled = arousal below threshold AND abs(valence) below threshold.
    """
    try:
        from ..cognition.milieu import get as _get_milieu

        milieu = _get_milieu()
        if milieu is None:
            return False, "milieu not initialized"
        s = milieu.get_state()
        aro = s.arousal
        val = s.valence
        settled = (
            abs(aro) < _BOREDOM_AROUSAL_THRESHOLD
            and abs(val) < _BOREDOM_VALENCE_THRESHOLD
        )
        return settled, f"arousal={aro:.3f} valence={val:.3f}"
    except Exception as e:
        return False, f"milieu error: {e}"


def _get_ef_wonder() -> str | None:
    """
    Pick a random EF question node, search for related memories, return a wonder string.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        eq_id = random.choice(_EF_NODE_IDS)
        eq_node = cortex.get(eq_id)
        if eq_node is None:
            return None
        question_text = eq_node.metadata.get("ef_question_text", eq_node.narrative[:80])

        # Search for nodes related to this EF question
        hits = cortex.search(question_text, limit=5)
        # Filter out the EF nodes themselves, pick something interesting
        hits = [h for h in hits if not h.id.startswith("EF_") and len(h.narrative) > 40]
        if not hits:
            return f"[Igor wonders] {question_text}"
        pick = hits[0]
        snippet = pick.narrative[:120].strip()
        return f"[Igor wonders] {question_text} — {snippet}..."
    except Exception as e:
        log.info(f"ef_wonder error: {e}")
        return None


def _get_topic_wonder() -> str | None:
    """
    Pick the topic with highest arousal_weight (with some randomization), search related content.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        # Read all topic nodes, sort by arousal_weight, pick from top 3
        topics = []
        for tid in _TOPIC_NODE_IDS:
            node = cortex.get(tid)
            if node:
                weight = float(node.metadata.get("arousal_weight", 0.5))
                topics.append((weight, node))
        if not topics:
            return None
        topics.sort(key=lambda x: x[0], reverse=True)
        # Pick randomly from top 3 to vary output
        pool = topics[:3]
        _, topic_node = random.choice(pool)
        topic_name = topic_node.metadata.get("topic", topic_node.id)

        # Search for hot content under this topic
        hits = cortex.search(topic_node.narrative[:200], limit=5)
        hits = [
            h
            for h in hits
            if not h.id.startswith("TOPIC_")
            and not h.id.startswith("TOPICS_")
            and len(h.narrative) > 40
        ]
        if not hits:
            return f"[Igor wonders about {topic_name}] — what's in there?"
        pick = hits[0]
        snippet = pick.narrative[:120].strip()
        return f"[Igor wonders about {topic_name}] {snippet}..."
    except Exception as e:
        log.info(f"topic_wonder error: {e}")
        return None


def run_boredom_check(**_) -> str:
    """
    D272: Check if milieu is too settled → run idle traversal → post wonder to channel.
    Rate-limited to IGOR_BOREDOM_COOLDOWN_SECONDS (default 15min) per post.
    """
    global _last_posted, _cycle_counter

    now = time.time()

    # Rate limit check
    if now - _last_posted < _COOLDOWN_SECONDS:
        elapsed = int(now - _last_posted)
        remaining = _COOLDOWN_SECONDS - elapsed
        return f"[boredom_idle] cooldown: {remaining}s remaining"

    # Milieu check
    is_bored, reason = _is_bored()
    log.info(f"CHECK bored={is_bored} {reason}")

    if not is_bored:
        return f"[boredom_idle] not settled — {reason} — no idle traversal"

    # Generate wonder
    _cycle_counter += 1
    wonder = None

    if _cycle_counter % 2 == 0:
        # Even cycles: EF question traversal
        wonder = _get_ef_wonder()
    else:
        # Odd cycles: topic traversal
        wonder = _get_topic_wonder()

    # Fallback to the other if primary fails
    if wonder is None:
        wonder = _get_ef_wonder() or _get_topic_wonder()

    if wonder is None:
        wonder = "[Igor notices] settled... nothing pulling at me right now."

    _post_to_channel(wonder)
    _last_posted = now
    log.info(f"POST {wonder[:120]}")

    return f"[boredom_idle] posted: {wonder[:80]}"


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_boredom_check",
        description=(
            "D272: Check if milieu is too settled (low arousal + near-neutral valence). "
            "If idle, traverse EF questions tree or topics tree, find related content, "
            "post '[Igor wonders]' to channel. Rate-limited. "
            "Called by PROC_BOREDOM_TRIGGER on 10-min schedule."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_boredom_check,
    )
)
