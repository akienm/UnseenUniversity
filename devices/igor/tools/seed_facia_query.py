#!/usr/bin/env python3
"""
Seed facia memory query pattern for Igor.

Usage:
  python3 wild_igor/igor/tools/seed_facia_query.py
"""

import json
import os
import sys
from datetime import datetime

from ..paths import paths as _paths
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])


def seed_facia_query():
    """Deposit INTERPRETIVE memory and PROC_QUERY_FACIA habit for facia discovery."""
    import psycopg2

    db_url = _paths().home_db_url

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # ── INTERP_FACIA_DISCOVERY: explain the facia query pattern ────
        interp_id = "INTERP_FACIA_DISCOVERY"
        interp_narrative = """Facia memory query pattern

Facia memories are entry points into named structures (like trees or tool groups).
They are indexed by ID pattern: INTERP_FACIA_*
They are INTERPRETIVE type with metadata.facia=true

To find facia memories:
  - Call list_facia_memories for a complete list
  - Search metadata with 'facia' keyword to find specific ones
  - Use the ID pattern directly for targeted queries

Each facia memory narrative starts with "Tool:" and describes what structure it indexes."""

        interp_metadata = {
            "type": "pattern_explanation",
            "pattern": "facia_discovery",
            "source": "claude-code",
            "facia": True,
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
                interp_id,
                "INTERPRETIVE",
                interp_narrative,
                json.dumps(interp_metadata),
                datetime.now().isoformat(),
            ),
        )

        print(f"Seeded {interp_id}")

        # ── PROC_QUERY_FACIA: habit that fires on facia query intent ────
        proc_id = "PROC_QUERY_FACIA"
        proc_narrative = """Query facia memory index when asked about entry points or structures.

When Igor asks "what facia memories do I have?", "list facia", "what entry points exist?", etc:
1. Call list_facia_memories to get the full list
2. Return formatted list with tool names and descriptions
3. Route response through normal synthesis (not canned 'On it')"""

        proc_metadata = {
            "trigger": "facia_discovery|list_facia|entry_points|what_facia",
            "code_ref": "tools/memory_query.py:list_facia_memories",
            "intent_types": ["question", "action_request"],
            "tags": ["self_query", "introspection", "structure_discovery"],
            "source": "claude-code",
            "provenance": "builtin",
            "trust_level": 0.85,
            "execution_permissions": ["read_memory"],
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
                proc_narrative,
                json.dumps(proc_metadata),
                datetime.now().isoformat(),
            ),
        )

        print(f"Seeded {proc_id}")

        conn.close()
        print("Facia query seeding complete.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(seed_facia_query())
