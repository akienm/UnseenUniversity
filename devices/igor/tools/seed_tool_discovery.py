#!/usr/bin/env python3
"""
Seed tool discovery habit and memory for Igor.

Usage:
  python3 wild_igor/igor/tools/seed_tool_discovery.py
"""

import json
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])


def seed_tool_discovery():
    """Deposit INTERPRETIVE memory about get_tool_registry_report and PROC_TOOL_DISCOVERY habit."""
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # ── INTERP_FACIA_TOOL_DISCOVERY: index/entry point for tool discovery capability ────
        ts = datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6]
        interp_id = f"INTERP_FACIA_TOOL_DISCOVERY"

        interp_narrative = """Tool: get_tool_registry_report

Igor has 191 registered tools. Call get_tool_registry_report to enumerate them:
  - No arguments: list all tools (name, description, parameters)
  - With 'filter' argument: filter by name/pattern

Triggers: "what tool", "list my tools", "what can i do", "tool list", "what tools"
Response: returns matching tools with descriptions."""

        interp_metadata = {
            "type": "tool_index",
            "tool_name": "get_tool_registry_report",
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

        # ── PROC_TOOL_DISCOVERY: habit that fires on tool-discovery intent ────
        proc_id = "PROC_TOOL_DISCOVERY"
        proc_narrative = """Discover available tools when asked what tools are available.

When Igor asks "what tools do I have?", "list my tools", "what can I do?", etc:
1. Call get_tool_registry_report with optional filter from context
2. Return tool list with descriptions
3. Route response through normal synthesis (not canned 'On it')"""

        proc_metadata = {
            "trigger": "tool_discovery|what_can_i_do|list_tools",
            "code_ref": "tools/operations.py:get_tool_registry_report",
            "intent_types": ["question", "action_request"],
            "tags": ["self_query", "introspection"],
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
        print("Tool discovery seeding complete.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(seed_tool_discovery())
