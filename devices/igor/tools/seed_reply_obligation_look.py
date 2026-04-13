#!/usr/bin/env python3
"""
seed_reply_obligation_look.py — Seed PROC_REPLY_OBLIGATION_LOOK habit.

T-reply-obligation-fork. Fires when Igor commits to look/check/find something
mid-conversation (e.g. "let me look at the ticket list", "thinking about that",
"i'll check"). When fired:

  - dispatcher detects awaiting_reply=true → calls goal_adopt with origin
    context (turn_id, thread_id, user_input) BEFORE spawning bg job
  - fork_bg=true → spawns the bg job that does the actual lookup
  - on completion, the drain bouquet pushes origin question, refreshed goal,
    and a pending_reply marker to TWM so the next reply emerges from
    salience competition (biomimicry framing — no direct compose call)

conversation_eligible=true so it can fire on conversation/general intents
(D354 pattern from twm-attentional-gating).
"""

import json
import os
import sys
from datetime import datetime

DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

# Narrow trigger surface — explicit commit-to-look phrases only.
# Wider triggers risk false positives on incidental "let me know" or similar.
TRIGGER = (
    "let me look at|let me check|let me find out|let me see|let me dig|"
    "i'll look|i'll check|i'll find out|i'll dig|"
    "thinking about that|i should check|give me a moment|one sec while i|"
    "let me look|let me think about that"
)


def seed():
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    meta = {
        "why": (
            "T-reply-obligation-fork: Igor commits to look/check/think mid-conversation "
            "but loses the obligation. The dispatcher's awaiting_reply branch adopts a "
            "GOAL carrying origin (thread, turn, question), spawns a bg job linked to "
            "that goal, and the completion drain surfaces a bouquet (origin question, "
            "goal refresh, pending_reply marker) to TWM. Reply emerges from salience "
            "competition — not from a direct composer call."
        ),
        "trigger": TRIGGER,
        "habit_type": "cognitive",
        "habit_score": 0.88,
        "fork_bg": True,
        "awaiting_reply": True,
        "conversation_eligible": True,
        "conditions": {"intent": ["conversation", "general"]},
        "inertia": 0.3,
        "provenance": "seed:T-reply-obligation-fork",
    }

    cur.execute(
        """
        INSERT INTO memories (id, memory_type, narrative, metadata, timestamp, activation_count)
        VALUES (%s, %s, %s, %s, %s, 1)
        ON CONFLICT (id) DO UPDATE
        SET narrative = EXCLUDED.narrative,
            metadata = EXCLUDED.metadata,
            activation_count = memories.activation_count + 1
        """,
        (
            "PROC_REPLY_OBLIGATION_LOOK",
            "PROCEDURAL",
            (
                "When I commit to look at, check, or think about something mid-conversation, "
                "adopt a reply-obligation goal carrying the originating turn context, then "
                "fork the actual work to a background job. On completion, the drain pushes "
                "the result, the origin question, and a pending_reply marker to TWM as a "
                "bouquet — the next reply emerges from salience competition, not from a "
                "direct composer call."
            ),
            json.dumps(meta),
            datetime.now().isoformat(),
        ),
    )
    print("  seeded PROC_REPLY_OBLIGATION_LOOK")

    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
