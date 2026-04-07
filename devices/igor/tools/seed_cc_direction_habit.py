#!/usr/bin/env python3
"""
Seed PROC_RECEIVE_CC_DIRECTION habit.

Usage:
  python3 wild_igor/igor/tools/seed_cc_direction_habit.py

Fires when Igor receives a long CC message that contains strategic direction
keywords. Calls receive_cc_direction() to deposit FACTUAL + TWM + ack.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, str(__file__).rsplit("/", 3)[0])


def seed():
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
    )

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    proc_id = "PROC_RECEIVE_CC_DIRECTION"
    narrative = """Receive and hold strategic direction from Claude Code.

When a CC message arrives containing direction keywords (decided, direction,
path, priority, from now on, you are now, goal is, we want, we wanna, moving
toward, going forward, focus on, strategic, D3xx, phase, milestone, new
approach, new direction), and the message is longer than a quick command:

1. Call receive_cc_direction(content) with the full message
2. Tool deposits FACTUAL node with identity_weight=0.9 (strategic context held)
3. Tool injects into TWM with 6-hour TTL so it shapes the next several turns
4. Tool posts [DIRECTION RECEIVED] acknowledgment to CC channel

This closes the matrix gap: without this habit, Igor works tickets but doesn't
know why. With it, D316/D317 decisions actually shape Igor's behavior."""

    metadata = {
        "trigger": "decided|direction|your path|from now on|D3[0-9][0-9]|you are now|goal is|we want|we wanna|moving toward|going forward|focus on|strategic|phase|milestone|new approach|new direction|priority",
        "code_ref": "tools/receive_cc_direction.py:receive_cc_direction",
        "habit_type": "tool",
        "intent_types": ["action_request", "conversational", "question"],
        "min_length": 80,
        "source": "claude-code",
        "provenance": "D316-D317",
        "trust_level": 0.95,
        "execution_permissions": ["write_memory", "twm_write", "channel_post"],
        "tags": ["strategic_direction", "matrix_gap_fix"],
    }

    cur.execute(
        """
        INSERT INTO memories
            (id, memory_type, narrative, metadata, timestamp, activation_count)
        VALUES (%s, %s, %s, %s, %s, 1)
        ON CONFLICT (id) DO UPDATE
        SET narrative = EXCLUDED.narrative,
            metadata = EXCLUDED.metadata,
            activation_count = memories.activation_count + 1
        """,
        (
            proc_id,
            "PROCEDURAL",
            narrative,
            json.dumps(metadata),
            datetime.now().isoformat(),
        ),
    )

    print(f"Seeded {proc_id}")
    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
