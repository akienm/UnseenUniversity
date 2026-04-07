#!/usr/bin/env python3
"""
Seed coding standards as graph nodes — so cortex.search() surfaces them
during HYPOTHESIZE when Igor writes code.

Usage:
  python3 wild_igor/igor/tools/seed_coding_standards.py
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, str(__file__).rsplit("/", 3)[0])

DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)

NODES = [
    (
        "CODING_STANDARDS_ROOT",
        "INTERPRETIVE",
        "Coding standards root — all rules for writing code in TheIgors codebase. "
        "See standards.dsb for full reference. Apply before any HYPOTHESIZE/IMPLEMENT step.",
        {"facia": True, "category": "coding_standards", "source": "standards.dsb"},
    ),
    (
        "RULE_NO_SILENT_EXCEPTIONS",
        "FACTUAL",
        "No silent exceptions. Every except block must log or re-raise. "
        "Wrong: except Exception: pass  or  return None silently. "
        "Right: log.error('[module ERROR] %s', e) then return '[module ERROR] {e}'. "
        "Background threads must catch all — unhandled exception kills thread silently.",
        {
            "category": "coding_standards",
            "rule": "no_silent_exceptions",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
    (
        "RULE_NO_BARE_EXCEPT",
        "FACTUAL",
        "No bare except. Always: except Exception as e — never bare except: "
        "Bare except catches KeyboardInterrupt and SystemExit, masking real errors.",
        {
            "category": "coding_standards",
            "rule": "no_bare_except",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
    (
        "RULE_TOOL_SIGNATURE",
        "FACTUAL",
        "Every tool function must accept **_ as final parameter. "
        "Habit dispatch passes extra keys — without **_ you get TypeError. "
        "Wrong: def my_tool(param: str)  Right: def my_tool(param: str, **_). "
        "code_ref habits auto-dispatch only for 1-required-arg tools.",
        {
            "category": "coding_standards",
            "rule": "tool_signature",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
    (
        "RULE_NEW_TOOL_CHECKLIST",
        "FACTUAL",
        "New tool checklist: (1) write function in tools/module.py "
        "(2) registry.register(Tool(...)) "
        "(3) add to tools/__init__.py — missing import = silent no-op "
        "(4) log_tool_call() at exit "
        "(5) log_error() in every except "
        "(6) test in tests/test_module.py "
        "(7) seed habit if needed.",
        {
            "category": "coding_standards",
            "rule": "new_tool_checklist",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
    (
        "RULE_DB_ACCESS",
        "FACTUAL",
        "Database is PostgreSQL only — never sqlite3. "
        "Read URL from IGOR_HOME_DB_URL env var. "
        "Use jsonb_exists(metadata,'key') not metadata ? 'key' — db_proxy breaks ?. "
        "Use json.dumps(dict) for metadata — str(dict) produces invalid JSON. "
        "ON CONFLICT: qualify table: memories.activation_count not just activation_count.",
        {
            "category": "coding_standards",
            "rule": "db_access",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
    (
        "RULE_LOG_TIMER",
        "FACTUAL",
        "Use get_timer for elapsed timing. "
        "Module-level: from ..logging_setup import get_timer; timer = get_timer(log, name, **ctx). "
        "IgorBase subclass: timer = self.log.get_timer(name, **ctx). "
        "timer.stop(**result) emits: name=X started=... elapsed=X.XXXXXX key=val. "
        "Never build manual t0/elapsed boilerplate.",
        {
            "category": "coding_standards",
            "rule": "log_timer",
            "parent": "CODING_STANDARDS_ROOT",
        },
    ),
]


def seed():
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    for node_id, mem_type, narrative, metadata in NODES:
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
                node_id,
                mem_type,
                narrative,
                json.dumps(metadata),
                datetime.now().isoformat(),
            ),
        )
        print(f"  seeded {node_id}")

    # Wire children to root
    for node_id, _, _, meta in NODES[1:]:
        cur.execute(
            "INSERT INTO interpretive_edges (from_id, to_id, direction) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            ("CODING_STANDARDS_ROOT", node_id, "child"),
        )

    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(seed())
