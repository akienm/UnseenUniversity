#!/usr/bin/env python3
"""
seed_layer4_run_bash.py — RUN_BASH Layer 4 programming TEMPLATE node.

RUN_BASH executes a shell command from the basket and writes output back.
Used for: running tests, git operations, grep, reading file listings.

Basket contract:
  Input:  bash_cmd (str | list[str])
  Output: bash_output (str — stdout+stderr, capped at 600 chars)

code_ref: pe_chain:pe_run_bash

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://unseen_university:<password>@127.0.0.1/unseen_university \\
        python claudecode/seed_layer4_run_bash.py

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["UU_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer4-run-bash"

TEMPLATE_SCHEMA = {
    "pattern_name": "RUN_BASH",
    "layer": 4,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 4 programming brick: run a shell command from basket, write output back. "
        "basket.bash_cmd is the command (str or list). "
        "basket.bash_output receives stdout+stderr (capped at 600 chars). "
        "FORKIF next_node when output is non-empty. "
        "General-purpose: run tests, git, grep, read file listing, etc."
    ),
    "basket_contract": {
        "reads": ["bash_cmd", "bash_timeout"],
        "writes": ["bash_output"],
        "side_effects": [
            "Executes arbitrary shell command — caller is responsible for safety",
            "FORKIF next_node when bash_output non-empty",
        ],
    },
    "slot_manifest": [
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": "Node ID to FORKIF after bash_output is written.",
        },
        {
            "name": "default_timeout",
            "required": False,
            "default": 30,
            "type_hint": "int",
            "description": "Default subprocess timeout in seconds. Default 30.",
            "validator": {"min": 5, "max": 300},
        },
    ],
    "expansion_schema": [
        {
            "id": "{{ prefix }}_RUN_BASH",
            "narrative": (
                "RUN_BASH: execute basket.bash_cmd shell command, emit bash_output. "
                "Reads bash_cmd (str|list) → calls pe_chain:pe_run_bash → "
                "emits basket.bash_output. "
                "{% if next_node %}FORKs to {{ next_node }} when output non-empty.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": "Expanded from tpl-layer4-run-bash. prefix={{ prefix }}",
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer4-run-bash",
                "layer": 4,
                "basket_reads": ["bash_cmd", "bash_timeout"],
                "basket_writes": ["bash_output"],
                "code_ref": "pe_chain:pe_run_bash",
                "triggers": {"__entry__": "run_bash_cell"},
                "inertia": 0.3,
                "why": (
                    "Shell execution is a primitive in the programming chain. "
                    "RUN_BASH makes it a named, basket-aware brick rather than an "
                    "ad-hoc subprocess call. Callers set bash_cmd before firing; "
                    "downstream nodes read bash_output. "
                    "Used for test runs (VERIFY_RESULT), git ops (COMMIT), "
                    "and environment inspection (READ_CODEBASE fallback)."
                ),
            },
            "payload": {
                "NARRATIVE": (
                    "RUN_BASH — execute shell command from basket. "
                    "Basket: reads bash_cmd, writes bash_output."
                ),
                "code_ref": "pe_chain:pe_run_bash",
                "default_timeout": "{{ default_timeout }}",
                "run_bash_cell": [
                    # Guard: no command, no-op
                    ["STOPIF", ["bash_cmd", "==", None]],
                    # Scaffold: pe_run_bash fills bash_output
                    ["EMITIF", True, "bash_output", "", "basket"],
                    # Fork when output produced
                    ["FORKIF", ["bash_output", "!=", ""], "{{ next_node }}"],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_RUN_BASH"],
        "condition_signature": {
            "triggers": {"__entry__": "run_bash_cell"},
            "basket_reads": ["bash_cmd"],
            "basket_writes": ["bash_output"],
        },
        "invariants": [
            "basket.bash_output is str after execution (may be empty on error)",
            "STOPIF fires on absent/null bash_cmd — no subprocess started",
            "bash_output capped at 600 chars to avoid overwhelming context",
            "FORKIF fires when bash_output non-empty AND next_node provided",
            "Caller is responsible for constructing safe bash_cmd values",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Use RUN_BASH → VERIFY_RESULT to run tests and interpret the output. "
            "Set bash_cmd = ['python', '-m', 'pytest', 'tests/', '-x', '-q'] before firing. "
            "VERIFY_RESULT reads bash_output to determine pass/fail."
        ),
    },
}

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "RUN_BASH — Layer 4 programming brick. "
        "Executes a shell command from basket.bash_cmd, writes stdout+stderr to "
        "basket.bash_output (capped at 600 chars). "
        "General-purpose: run tests, git operations, grep, file listings. "
        "The only subprocess-executing node in the layer 4 chain."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": "T-programming-engrams-layer4: Layer 4 RUN_BASH brick.",
    "metadata": {
        "template": True,
        "schema_version": 1,
        "layer": 4,
        "pattern_name": "RUN_BASH",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer4", "programming", "run_bash", "subprocess", "basket_contract"],
        "inertia": 0.3,
        "why": (
            "Shell execution is a primitive that appears in multiple programming steps: "
            "running tests, committing changes, reading directory listings. "
            "Making it a named basket-aware brick gives Igor a composable, auditable "
            "primitive rather than scattered subprocess.run calls."
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
