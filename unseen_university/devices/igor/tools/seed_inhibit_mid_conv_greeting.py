#!/usr/bin/env python3
"""
seed_inhibit_mid_conv_greeting.py — Fix PROC_GREET_AKIEN + seed mid-conv greeting suppressor.

Two changes:
1. PROC_GREET_AKIEN: remove "akien" from trigger (fires on every "TALKING WITH: Akien" header).
   Add conditions={intent:["greeting"]} so it only fires on genuine greeting intent.
2. PROC_SUPPRESS_MID_CONV_GREETING (new): passive_capture at 0.97.
   Fires when greeting words appear BUT intent is NOT greeting.
   Wins over PROC_GREETING (0.95) and GREETING_STANDARD (0.9) → falls through to LLM.
   Effect: "hi" in a mid-conversation message gets a content response, not "Hello. Ready when you are."

Closes T-inhibit-mid-conv-greeting.
"""
import json
import os
import sys
from datetime import datetime

from ..paths import paths as _paths
sys.path.insert(0, str(__file__).rsplit("/", 3)[0])

DB_URL = _paths().home_db_url

# Greeting trigger words — same as the GREETING_SPACE tree, minus "akien"
GREETING_TRIGGER = "hello|hi|hey|welcome|greetings|good morning|good evening|howdy"


def seed():
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    # ── 1. Fix PROC_GREET_AKIEN ──────────────────────────────────────────────
    # Was: trigger includes "akien" → fired on every "TALKING WITH: Akien" header
    # Fix: remove "akien", add conditions={intent:["greeting"]}
    fixed_meta = {
        "why": "Intercept greetings — fires only on genuine greeting intent. Old trigger had 'akien' which fired on every message header.",
        "trigger": GREETING_TRIGGER,
        "conditions": {"intent": ["greeting"]},
        "match_mode": "conditions_first",
        "habit_type": "response",
        "habit_score": 0.92,
        "response_template": "Hello, Akien! What would you like to tackle?",
    }
    cur.execute(
        """
        UPDATE memories
        SET metadata = %s,
            narrative = 'Greet Akien when they open a fresh conversation. '
                        'Fires only on genuine greeting intent — not mid-conversation.'
        WHERE id = 'PROC_GREET_AKIEN'
        """,
        (json.dumps(fixed_meta),),
    )
    print(f"  fixed PROC_GREET_AKIEN (rows: {cur.rowcount})")

    # ── 2. Seed PROC_SUPPRESS_MID_CONV_GREETING ──────────────────────────────
    # passive_capture at 0.97 — beats greeting habits (0.95 / 0.9)
    # Fires when greeting trigger words appear BUT intent is NOT "greeting"
    # → not a real greeting → fall through to LLM for content response
    supp_meta = {
        "why": "Mid-conversation greeting suppressor. When greeting words appear but intent "
               "is not 'greeting', it means the LLM or user used a greeting word incidentally "
               "— not a fresh hello. High score (0.97) wins over PROC_GREET_AKIEN (0.92), "
               "PROC_GREETING (0.95), GREETING_STANDARD (0.9). passive_capture falls through "
               "to LLM for a content response instead of emitting 'Hello.'",
        "trigger": GREETING_TRIGGER,
        "conditions": {"not_intent": ["greeting"]},
        "match_mode": "conditions_first",
        "habit_type": "passive_capture",
        "habit_score": 0.97,
        "provenance": "seed:T-inhibit-mid-conv-greeting",
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
            "PROC_SUPPRESS_MID_CONV_GREETING",
            "PROCEDURAL",
            "Suppress mid-conversation greetings. When greeting words appear but intent is not "
            "'greeting', fall through to LLM for content-appropriate response instead of "
            "emitting a generic hello.",
            json.dumps(supp_meta),
            datetime.now().isoformat(),
        ),
    )
    print(f"  seeded PROC_SUPPRESS_MID_CONV_GREETING")

    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
