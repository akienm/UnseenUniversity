#!/usr/bin/env python3
"""
seed_layer4_verify_result.py — VERIFY_RESULT Layer 4 programming TEMPLATE node.

VERIFY_RESULT runs the test suite and routes the basket on pass/fail.
Composes TEST + SCOPE_CHECK (layer 3 bricks).

Basket contract:
  Input:  (none required — runs test suite directly)
  Output: test_result (str — "pass" | "fail: <details>"), verify_passed (bool)

code_ref: pe_chain:pe_test

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer4_verify_result.py

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["UU_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer4-verify-result"

TEMPLATE_SCHEMA = {
    "pattern_name": "VERIFY_RESULT",
    "layer": 4,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 4 programming brick: run test suite, route basket on pass/fail. "
        "Composes TEST + SCOPE_CHECK (layer 3 bricks). "
        "Emits basket.test_result ('pass' | 'fail: <details>') and basket.verify_passed (bool). "
        "BRANCHIF routes to pass_node on pass, fail_node on fail."
    ),
    "basket_contract": {
        "reads": [],
        "writes": ["test_result", "verify_passed"],
        "side_effects": [
            "Runs pytest test suite (subprocess or ops.run_tests)",
            "BRANCHIF pass_node when test_result == 'pass'",
            "BRANCHIF fail_node when test_result starts with 'fail'",
        ],
    },
    "slot_manifest": [
        {
            "name": "pass_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to branch to when tests pass.",
        },
        {
            "name": "fail_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to branch to when tests fail (for REPLAN).",
        },
    ],
    "expansion_schema": [
        {
            "id": "{{ prefix }}_VERIFY_RESULT",
            "narrative": (
                "VERIFY_RESULT: run test suite, emit test_result + verify_passed. "
                "Calls pe_chain:pe_test → basket.test_result. "
                "{% if pass_node %}BRANCHes to {{ pass_node }} on pass.{% endif %}"
                "{% if fail_node %}BRANCHes to {{ fail_node }} on fail.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": "Expanded from tpl-layer4-verify-result. prefix={{ prefix }}",
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer4-verify-result",
                "layer": 4,
                "basket_reads": [],
                "basket_writes": ["test_result", "verify_passed"],
                "code_ref": "pe_chain:pe_test",
                "triggers": {"__entry__": "verify_result_cell"},
                "inertia": 0.3,
                "why": (
                    "Tests are the ground truth for whether a code change worked. "
                    "VERIFY_RESULT is the final gate before COMMIT. "
                    "The dual BRANCHIF (pass/fail) cleanly routes to COMMIT or REPLAN "
                    "without conditionals in the caller."
                ),
            },
            "payload": {
                "NARRATIVE": (
                    "VERIFY_RESULT — run test suite, route on pass/fail. "
                    "Basket: writes test_result + verify_passed."
                ),
                "code_ref": "pe_chain:pe_test",
                "verify_result_cell": [
                    # Scaffold defaults (pe_test overwrites test_result)
                    ["EMITIF", True, "test_result", "pending", "basket"],
                    ["EMITIF", True, "verify_passed", False, "basket"],
                    # Route: pass → commit, fail → replan
                    [
                        "BRANCHIF",
                        ["test_result", "==", "pass"],
                        "{{ pass_node }}",
                        "{{ fail_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_VERIFY_RESULT"],
        "condition_signature": {
            "triggers": {"__entry__": "verify_result_cell"},
            "basket_reads": [],
            "basket_writes": ["test_result", "verify_passed"],
        },
        "invariants": [
            "basket.test_result is 'pass' or starts with 'fail' after execution",
            "basket.verify_passed is bool after execution",
            "BRANCHIF routes exclusively to pass_node or fail_node — never both",
            "No guard STOPIF needed: pe_test always produces a result",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain PATCH_FILE → VERIFY_RESULT → COMMIT (pass) / REPLAN (fail). "
            "Set pass_node to COMMIT node ID; fail_node to REPLAN node ID. "
            "The BRANCHIF in verify_result_cell routes exclusively — "
            "one branch fires, the other is a no-op when its target is 'None'."
        ),
    },
}

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "VERIFY_RESULT — Layer 4 programming brick. "
        "Runs the test suite (pe_chain:pe_test) and emits basket.test_result "
        "('pass' | 'fail: <details>') + basket.verify_passed (bool). "
        "BRANCHIF routes to COMMIT on pass, REPLAN on fail. "
        "The final gate before a code change is committed."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": "T-programming-engrams-layer4: Layer 4 VERIFY_RESULT brick.",
    "metadata": {
        "template": True,
        "schema_version": 1,
        "layer": 4,
        "pattern_name": "VERIFY_RESULT",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer4", "programming", "verify_result", "test", "basket_contract"],
        "inertia": 0.3,
        "why": (
            "Tests are the truth. Every programming chain must end at a verification "
            "gate before committing. VERIFY_RESULT makes this gate a named, composable "
            "brick — not an implicit check buried in close_loop. "
            "The dual BRANCHIF pattern makes routing explicit and testable."
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
