#!/usr/bin/env python3
"""
seed_layer4_write_test.py — WRITE_TEST Layer 4 programming TEMPLATE node.

WRITE_TEST generates a pytest test for a planned code change.
Specialises the HYPOTHESIZE layer 3 brick with a test-generation prompt:
given ticket_description + actual (observed code), produce test code that
would verify the change works.

Basket contract:
  Input:  ticket_description (str), actual (str — observed code section)
  Output: test_code (str — pytest test function body), test_confidence (float)

code_ref: pe_chain:pe_hypothesize (with basket["test_mode"] = True)

Usage:
    cd ~/TheIgors && source venv/bin/activate
    IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer4_write_test.py

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["IGOR_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer4-write-test"

TEMPLATE_SCHEMA = {
    "pattern_name": "WRITE_TEST",
    "layer": 4,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 4 programming brick: generate a pytest test for a planned code change. "
        "Specialises HYPOTHESIZE (layer 3) with a test-generation prompt. "
        "Reads basket.ticket_description + basket.actual → "
        "emits basket.test_code (pytest function string) + basket.test_confidence. "
        "FORKIF next_node when test_code is non-empty."
    ),
    "basket_contract": {
        "reads": ["ticket_description", "actual"],
        "writes": ["test_code", "test_confidence"],
        "side_effects": [
            "tier.2 Ollama call to generate test code",
            "FORKIF next_node when test_code non-empty",
        ],
    },
    "slot_manifest": [
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to FORKIF after test_code generated. Null = standalone.",
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.5,
            "type_hint": "float",
            "description": "Scaffold test_confidence when code_ref does not emit one.",
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            "id": "{{ prefix }}_WRITE_TEST",
            "narrative": (
                "WRITE_TEST: generate pytest test for planned code change. "
                "Reads basket.ticket_description + basket.actual → "
                "calls pe_chain:pe_hypothesize (test_mode=True) → "
                "emits basket.test_code + basket.test_confidence. "
                "{% if next_node %}FORKs to {{ next_node }} when test_code non-empty.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": "Expanded from tpl-layer4-write-test. prefix={{ prefix }}",
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer4-write-test",
                "layer": 4,
                "basket_reads": ["ticket_description", "actual"],
                "basket_writes": ["test_code", "test_confidence"],
                "code_ref": "pe_chain:pe_hypothesize",
                "triggers": {"__entry__": "write_test_cell"},
                "inertia": 0.3,
                "why": (
                    "Test-first programming requires generating the test before "
                    "writing the implementation. WRITE_TEST makes this a named "
                    "basket-aware step rather than an implicit add-on. "
                    "Uses pe_hypothesize with test_mode=True so the prompt "
                    "requests test code rather than an old→new replacement dict."
                ),
            },
            "payload": {
                "NARRATIVE": (
                    "WRITE_TEST — generate pytest test for a planned code change. "
                    "Basket: reads ticket_description + actual, writes test_code + test_confidence."
                ),
                "code_ref": "pe_chain:pe_hypothesize",
                "test_mode": True,
                "default_confidence": "{{ default_confidence }}",
                "write_test_cell": [
                    # Guard: need both description and observed code
                    ["STOPIF", ["ticket_description", "==", None]],
                    ["STOPIF", ["actual", "==", None]],
                    ["STOPIF", ["actual", "==", ""]],
                    # Scaffold defaults (code_ref fills test_code via tier.2 call)
                    ["EMITIF", True, "test_code", "", "basket"],
                    [
                        "EMITIF",
                        True,
                        "test_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Fork to next node when test code generated
                    ["FORKIF", ["test_code", "!=", ""], "{{ next_node }}"],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_WRITE_TEST"],
        "condition_signature": {
            "triggers": {"__entry__": "write_test_cell"},
            "basket_reads": ["ticket_description", "actual"],
            "basket_writes": ["test_code", "test_confidence"],
        },
        "invariants": [
            "basket.test_code is str after execution (empty if tier.2 unavailable or actual empty)",
            "basket.test_confidence is float 0.0–1.0 after execution",
            "STOPIF fires on absent ticket_description OR absent/empty actual",
            "FORKIF fires when test_code non-empty AND next_node provided",
            "test_mode=True in payload signals pe_hypothesize to use test-generation prompt",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain READ_CODEBASE → WRITE_TEST → PATCH_FILE for test-first programming. "
            "WRITE_TEST fires after READ_CODEBASE populates basket.actual. "
            "test_code is then written to a test file via a PATCH_FILE node. "
            "After PATCH_FILE, VERIFY_RESULT runs the test to confirm it works."
        ),
    },
}

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "WRITE_TEST — Layer 4 programming brick. "
        "Generates a pytest test for a planned code change. "
        "Specialises HYPOTHESIZE (layer 3) with a test-generation prompt: "
        "given ticket_description + actual code, produce test code that verifies the change. "
        "Enables test-first programming within the engram chain."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": "T-programming-engrams-layer4: Layer 4 WRITE_TEST brick.",
    "metadata": {
        "template": True,
        "schema_version": 1,
        "layer": 4,
        "pattern_name": "WRITE_TEST",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer4",
            "programming",
            "write_test",
            "hypothesize",
            "test_first",
            "basket_contract",
        ],
        "inertia": 0.3,
        "why": (
            "Tests before code is the professional standard. WRITE_TEST gives the "
            "engram chain the ability to generate tests for a planned change before "
            "writing the implementation — the same discipline Claude Code follows. "
            "Reuses pe_hypothesize (already has the tier.2 call + parsing logic) "
            "with test_mode=True to request test output instead of a replacement dict."
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
