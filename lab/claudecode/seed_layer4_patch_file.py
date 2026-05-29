#!/usr/bin/env python3
"""
seed_layer4_patch_file.py — PATCH_FILE Layer 4 programming TEMPLATE node.

PATCH_FILE applies a structured edit (hypothesis) to a source file.
Wraps the IMPLEMENT layer 3 brick as a named, standalone programming operation.

Basket contract:
  Input:  hypothesis (dict — {file: str, old_string: str, new_string: str})
  Output: implement_result (str), implement_skipped (bool)

code_ref: pe_chain:pe_implement

Usage:
    cd ~/TheIgors && source venv/bin/activate
    IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer4_patch_file.py

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["IGOR_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer4-patch-file"

TEMPLATE_SCHEMA = {
    "pattern_name": "PATCH_FILE",
    "layer": 4,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 4 programming brick: apply structured edit (hypothesis) to a source file. "
        "Reads basket.hypothesis ({file, old_string, new_string}), applies edit in-place. "
        "FORKIF pass_node on success; FORKIF fail_node on skipped/error. "
        "Wraps IMPLEMENT (layer 3) as a named programming operation."
    ),
    "basket_contract": {
        "reads": ["hypothesis"],
        "writes": ["implement_result", "implement_skipped"],
        "side_effects": [
            "Writes new_string into hypothesis.file (replaces old_string, first occurrence)",
            "FORKIF pass_node on implement_skipped == False",
            "FORKIF fail_node on implement_skipped == True",
        ],
    },
    "slot_manifest": [
        {
            "name": "pass_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to FORKIF when edit applied successfully (implement_skipped=False).",
        },
        {
            "name": "fail_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to FORKIF when edit skipped/errored (implement_skipped=True).",
        },
    ],
    "expansion_schema": [
        {
            "id": "{{ prefix }}_PATCH_FILE",
            "narrative": (
                "PATCH_FILE: apply basket.hypothesis edit to source file. "
                "Reads {file, old_string, new_string} → calls pe_chain:pe_implement → "
                "emits basket.implement_result + basket.implement_skipped. "
                "{% if pass_node %}FORKs to {{ pass_node }} on success.{% endif %}"
                "{% if fail_node %}FORKs to {{ fail_node }} on skip/error.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": "Expanded from tpl-layer4-patch-file. prefix={{ prefix }}",
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer4-patch-file",
                "layer": 4,
                "basket_reads": ["hypothesis"],
                "basket_writes": ["implement_result", "implement_skipped"],
                "code_ref": "pe_chain:pe_implement",
                "triggers": {"__entry__": "patch_file_cell"},
                "inertia": 0.3,
                "why": (
                    "Isolates the file-write step so WRITE_TEST and VERIFY_RESULT "
                    "can be chained independently. PATCH_FILE is the only node that "
                    "touches the filesystem — making it a named brick simplifies auditing."
                ),
            },
            "payload": {
                "NARRATIVE": (
                    "PATCH_FILE — apply hypothesis edit to source file. "
                    "Basket: reads hypothesis, writes implement_result + implement_skipped."
                ),
                "code_ref": "pe_chain:pe_implement",
                "patch_file_cell": [
                    # Guard: no hypothesis, no-op
                    ["STOPIF", ["hypothesis", "==", None]],
                    # Scaffold defaults (pe_implement overwrites these)
                    ["EMITIF", True, "implement_result", "skipped", "basket"],
                    ["EMITIF", True, "implement_skipped", True, "basket"],
                    # Fork on success path
                    ["FORKIF", ["implement_skipped", "==", False], "{{ pass_node }}"],
                    # Fork on failure path
                    ["FORKIF", ["implement_skipped", "==", True], "{{ fail_node }}"],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_PATCH_FILE"],
        "condition_signature": {
            "triggers": {"__entry__": "patch_file_cell"},
            "basket_reads": ["hypothesis"],
            "basket_writes": ["implement_result", "implement_skipped"],
        },
        "invariants": [
            "basket.implement_result is str after execution",
            "basket.implement_skipped is bool after execution",
            "STOPIF fires on absent/null hypothesis — no file write attempted",
            "File is written only when hypothesis.old_string found verbatim",
            "FORKIF pass_node fires when implement_skipped is False",
            "FORKIF fail_node fires when implement_skipped is True",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain READ_CODEBASE → HYPOTHESIZE → PATCH_FILE → VERIFY_RESULT. "
            "Set pass_node to VERIFY_RESULT node ID; fail_node to REPLAN node ID. "
            "The dual FORKIF routes success and failure paths to different nodes."
        ),
    },
}

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "PATCH_FILE — Layer 4 programming brick. "
        "Applies a structured edit (hypothesis dict with file/old_string/new_string) "
        "to a source file. The only filesystem-writing node in the layer 4 chain. "
        "Routes success to VERIFY_RESULT, failure to REPLAN."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": "T-programming-engrams-layer4: Layer 4 PATCH_FILE brick.",
    "metadata": {
        "template": True,
        "schema_version": 1,
        "layer": 4,
        "pattern_name": "PATCH_FILE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer4", "programming", "patch_file", "implement", "basket_contract"],
        "inertia": 0.3,
        "why": (
            "Isolating the file-write step into a named brick makes it auditable "
            "and independently re-runnable. The dual FORKIF (pass/fail) enables "
            "clean routing to VERIFY_RESULT or REPLAN without conditionals in the "
            "caller. Code is the player; data (hypothesis) is the character."
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
