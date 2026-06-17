#!/usr/bin/env python3
"""
seed_layer4_read_codebase.py — READ_CODEBASE Layer 4 programming TEMPLATE node.

READ_CODEBASE is the first brick in Igor's code-sprint chain (layer 4).
Given ticket_description in the basket (+ optional plan_files hint), it:
  1. Situates which files need to change (SITUATE layer 3 brick)
  2. Reads relevant sections via grep+read (OBSERVE layer 3 brick)
  3. Emits basket.actual (code sections) for downstream HYPOTHESIZE/PATCH_FILE.

Composes layer 3 bricks: SITUATE → OBSERVE.
code_ref: pe_chain:pe_situate then pe_chain:pe_observe (sequential pair).

Basket contract:
  Input:  ticket_description (str), plan_files (list[str], optional — [] triggers tier.2 situate)
  Output: plan_files (resolved), actual (str), line_ranges (dict), observe_hits (int),
          situate_source (str)

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer4_read_codebase.py

Verify:
    Igor: memory_get("tpl-layer4-read-codebase")
    Igor: list_templates()

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["UU_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer4-read-codebase"

TEMPLATE_SCHEMA = {
    "pattern_name": "READ_CODEBASE",
    "layer": 4,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 4 programming brick: situate files + read relevant sections into basket.actual. "
        "Composes SITUATE + OBSERVE (layer 3 bricks). "
        "plan_files already set → skips tier.2 situate call (fast path). "
        "plan_files empty → calls tier.2 to identify files (slow path). "
        "FORKIF next_node chains to HYPOTHESIZE, PATCH_FILE, or WRITE_TEST."
    ),
    "basket_contract": {
        "reads": ["ticket_description", "plan_files"],
        "writes": [
            "plan_files",
            "actual",
            "line_ranges",
            "observe_hits",
            "situate_source",
        ],
        "side_effects": [
            "FORKIF next_node when actual is non-empty",
            "tier.2 Ollama call when plan_files is empty (SITUATE slow path)",
        ],
    },
    "slot_manifest": [
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after actual is populated. "
                "Null = standalone. Set to HYPOTHESIZE or PATCH_FILE node ID for chain."
            ),
        },
        {
            "name": "observe_context_lines",
            "required": False,
            "default": 40,
            "type_hint": "int",
            "description": "Lines of context before/after grep hit. Default 40.",
            "validator": {"min": 10, "max": 120},
        },
    ],
    "expansion_schema": [
        {
            "id": "{{ prefix }}_READ_CODEBASE",
            "narrative": (
                "READ_CODEBASE: situate files + read relevant sections into basket.actual. "
                "Reads basket.ticket_description (+ optional plan_files) → "
                "calls pe_chain:pe_situate then pe_chain:pe_observe → "
                "emits basket.plan_files + basket.actual + basket.line_ranges + basket.observe_hits. "
                "{% if next_node %}FORKs to {{ next_node }} when actual non-empty.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer4-read-codebase. " "prefix={{ prefix }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer4-read-codebase",
                "layer": 4,
                "basket_reads": ["ticket_description", "plan_files"],
                "basket_writes": [
                    "plan_files",
                    "actual",
                    "line_ranges",
                    "observe_hits",
                    "situate_source",
                ],
                "code_ref": "pe_chain:pe_situate",
                "code_ref_2": "pe_chain:pe_observe",
                "triggers": {"__entry__": "read_codebase_cell"},
                "inertia": 0.3,
                "why": (
                    "First brick in Layer 4 programming chain. "
                    "Isolates codebase reading from hypothesis generation. "
                    "Composes SITUATE + OBSERVE so downstream nodes always receive "
                    "basket.actual (the relevant code section). "
                    "plan_files fast-path avoids redundant tier.2 calls when ticket "
                    "already declares required_files."
                ),
            },
            "payload": {
                "NARRATIVE": (
                    "READ_CODEBASE — situate files + read relevant sections. "
                    "Basket contract: reads ticket_description + plan_files, "
                    "writes actual + line_ranges + observe_hits + situate_source."
                ),
                "code_ref": "pe_chain:pe_situate",
                "code_ref_2": "pe_chain:pe_observe",
                "observe_context_lines": "{{ observe_context_lines }}",
                "read_codebase_cell": [
                    # Guard: no input, no-op
                    ["STOPIF", ["ticket_description", "==", None]],
                    # Scaffold: pe_situate fills plan_files (fast or slow path)
                    ["EMITIF", True, "plan_files", [], "basket"],
                    # Scaffold: pe_observe fills actual with file sections
                    ["EMITIF", True, "actual", "", "basket"],
                    # Scaffold: observe metrics
                    ["EMITIF", True, "observe_hits", 0, "basket"],
                    ["EMITIF", True, "situate_source", "empty", "basket"],
                    # Fork to next programming node when actual populated
                    ["FORKIF", ["observe_hits", "!=", 0], "{{ next_node }}"],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_READ_CODEBASE"],
        "condition_signature": {
            "triggers": {"__entry__": "read_codebase_cell"},
            "basket_reads": ["ticket_description", "plan_files"],
            "basket_writes": ["plan_files", "actual", "line_ranges", "observe_hits"],
        },
        "invariants": [
            "basket.actual is str after execution (empty string if no files found)",
            "basket.plan_files is list after execution (may be empty)",
            "basket.observe_hits is int >= 0",
            "STOPIF fires on absent/null ticket_description — no writes",
            "FORKIF fires when observe_hits > 0 AND next_node provided at expansion time",
            "plan_files fast-path: if basket.plan_files non-empty, skips tier.2 situate call",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain READ_CODEBASE → WRITE_TEST or PATCH_FILE by setting next_node slot. "
            "The FORKIF spawns when actual is populated (observe_hits > 0). "
            "If no files found: basket.actual is empty, FORKIF does not fire — "
            "caller should handle empty-actual case (escalate or broaden search)."
        ),
    },
}

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "READ_CODEBASE — Layer 4 programming brick. "
        "Situates which files to change (SITUATE layer 3 brick) then reads relevant "
        "sections via grep+read (OBSERVE layer 3 brick). "
        "Emits basket.actual (code section string) for downstream HYPOTHESIZE / PATCH_FILE. "
        "plan_files fast-path skips tier.2 when ticket declares required_files."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": "T-programming-engrams-layer4: Layer 4 READ_CODEBASE brick.",
    "metadata": {
        "template": True,
        "schema_version": 1,
        "layer": 4,
        "pattern_name": "READ_CODEBASE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer4",
            "programming",
            "read_codebase",
            "situate",
            "observe",
            "basket_contract",
        ],
        "inertia": 0.3,
        "why": (
            "Programming tasks require reading before writing. READ_CODEBASE isolates "
            "the 'find the relevant code' step so PATCH_FILE and WRITE_TEST nodes can "
            "assume basket.actual is always populated. "
            "Composes two layer 3 bricks (SITUATE + OBSERVE) into a single named "
            "layer 4 operation — the programming-specific analogue of PARSE_GOAL."
        ),
    },
}


def seed(db_url: str) -> None:
    import psycopg2

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    now = datetime.now().isoformat()

    cur.execute(
        """
        INSERT INTO memories
            (id, narrative, memory_type, source, confidence,
             context_of_encoding, timestamp, updated_at,
             metadata, portable, scope)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'class')
        ON CONFLICT (id) DO UPDATE SET
            narrative          = EXCLUDED.narrative,
            metadata           = EXCLUDED.metadata,
            updated_at         = EXCLUDED.updated_at
        """,
        (
            TEMPLATE_NODE["id"],
            TEMPLATE_NODE["narrative"],
            TEMPLATE_NODE["memory_type"],
            TEMPLATE_NODE["source"],
            TEMPLATE_NODE["confidence"],
            TEMPLATE_NODE["context_of_encoding"],
            now,
            now,
            json.dumps(TEMPLATE_NODE["metadata"]),
        ),
    )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Seeded TEMPLATE node: {TEMPLATE_ID}")


if __name__ == "__main__":
    seed(DB_URL)
