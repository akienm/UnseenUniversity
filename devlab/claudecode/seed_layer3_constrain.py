#!/usr/bin/env python3
"""
seed_layer3_constrain.py — CONSTRAIN Layer 3 cognitive TEMPLATE node (D297).

CONSTRAIN checks the current plan (sub_goals + risk_signals) against known
constraints — inertia levels, scope boundaries, architecture patterns from cortex.
Emits `constraint_ok` (bool) and `violations` (list) back to the basket.

Basket contract:
  Input:  sub_goals (list), risk_signals (dict)
  Output: constraint_ok (bool), violations (list)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual constraint-checking tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:<password>@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_constrain.py

Verify:
    Igor: memory_get("tpl-layer3-constrain")
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

TEMPLATE_ID = "tpl-layer3-constrain"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix      (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                   → produces MYBOT_CONSTRAIN
#   code_ref    (str, required)   — tool fn to call for constraint checking,
#                                   e.g. "ops:check_constraints"
#   next_node   (str, optional)   — node ID to FORKIF to after emitting
#                                   (default: null — no fork)
#   strict_mode (bool, optional)  — when True, STOPIF fires on any violations
#                                   (default: False — optimistic; caller branches
#                                   on constraint_ok value)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the constrain executor.
#   The habit's payload has a single cell ("constrain_cell") that:
#     1. STOPIFs if sub_goals is absent (guard)
#     2. EMITIFs True to basket.constraint_ok (optimistic default — real tool
#        does actual constraint checking against cortex)
#     3. EMITIFs [] to basket.violations (empty default — real tool populates)
#     4. FORKIFs to next_node regardless of constraint_ok pass/fail
#        (caller branches on constraint_ok value after the fork)
#
# Note: real constraint checking happens inside the code_ref tool. The template
# wires the basket contract; the instantiator provides the tool. This keeps the
# scaffold decoupled from any particular inertia/scope implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["sub_goals", "==", null]]         -- guard: no plan, no-op
#   EMITIF  [True, "constraint_ok", True, "basket"]
#            ^^ optimistic default; code_ref override does real checking
#   EMITIF  [True, "violations", [], "basket"]
#            ^^ empty default; code_ref populates on actual violations
#   FORKIF  [["constraint_ok", "!=", None], "{{ next_node }}"]
#            ^^ fork after check regardless of pass/fail; caller branches on value
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "CONSTRAIN",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: check plan (sub_goals + risk_signals) against known "
        "constraints — inertia levels, scope boundaries, architecture patterns from cortex. "
        "Wires basket contract (sub_goals + risk_signals → constraint_ok + violations). "
        "code_ref slot is the constraint-checking tool — pluggable, not hardcoded. "
        "FORKIF next_node chains to next planning brick after check (caller branches on "
        "constraint_ok value). Used in both the basic plan chain (after DECOMPOSE) and "
        "the debug loop (after HYPOTHESIZE)."
    ),
    "basket_contract": {
        "reads": ["sub_goals", "risk_signals"],
        "writes": ["constraint_ok", "violations"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "writes CONSTRAINT_VIOLATION=violations to cognitive_milieu when constraint_ok is False (D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_CONSTRAIN.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for constraint checking, e.g. 'ops:check_constraints'. "
                "Must accept sub_goals + risk_signals from basket and return "
                "(constraint_ok, violations)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting constraint_ok. "
                "Null = standalone (no chaining). "
                "Set to EXECUTE or HYPOTHESIZE node ID for full planning-chain composition. "
                "Fork fires regardless of constraint_ok value — caller branches on the bool."
            ),
        },
        {
            "name": "strict_mode",
            "required": False,
            "default": False,
            "type_hint": "bool",
            "description": (
                "When True, STOPIF fires on any violations (violations list non-empty). "
                "Default False — optimistic; FORKIF fires and caller branches on constraint_ok."
            ),
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_CONSTRAIN
            "id": "{{ prefix }}_CONSTRAIN",
            "narrative": (
                "CONSTRAIN: check plan against known constraints (inertia, scope, architecture). "
                "Reads basket.sub_goals + basket.risk_signals → calls {{ code_ref }} → "
                "emits basket.constraint_ok + basket.violations. "
                "{% if next_node %}FORKs to {{ next_node }} after check.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-constrain (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-constrain",
                "layer": 3,
                "basket_reads": ["sub_goals", "risk_signals"],
                "basket_writes": ["constraint_ok", "violations"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "constrain_cell"
                },
                "inertia": 0.3,
                "why": (
                    "Layer 3 cognitive planning chain (D297). CONSTRAIN is the guard brick: "
                    "checks the decomposed plan against architecture constraints before execution. "
                    "Isolates constraint checking from decomposition and execution. "
                    "Used in both the basic plan chain (after DECOMPOSE) and the debug loop "
                    "(after HYPOTHESIZE) — constraint_ok bool gates execution or retry. "
                    "Optimistic default (True) ensures scaffold runs; real tool populates violations."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "CONSTRAIN node — check plan against known constraints. "
                    "Basket contract: reads sub_goals + risk_signals, "
                    "writes constraint_ok (bool) + violations (list)."
                ),
                "code_ref": "{{ code_ref }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against empty input (null/missing sub_goals).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF writes constraint_ok = True (optimistic scaffold default).
                #     Real constraint checking happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the constraint logic.
                #     True = "no violations found" is a valid optimistic default.
                #   - Second EMITIF writes violations = [] (empty default).
                #     Real impl populates this list with violated constraint names.
                #   - FORKIF fires when constraint_ok is set (non-None), regardless of value.
                #     "!= None" — eval_gate handles None/null comparison.
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #     The caller (EXECUTE, HYPOTHESIZE) branches on constraint_ok value.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "constrain_cell": [
                    # Guard: stop if sub_goals is absent or null
                    ["STOPIF", ["sub_goals", "==", None]],
                    # Emit constraint_ok = True (scaffold optimistic default)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that performs actual constraint
                    # checking against cortex (inertia levels, scope boundaries, arch patterns).
                    # The scaffold uses True as a valid default (no violations found).
                    ["EMITIF", True, "constraint_ok", True, "basket"],
                    # Emit violations = [] (scaffold empty default)
                    # Real impl populates with violated constraint names/descriptions.
                    ["EMITIF", True, "violations", [], "basket"],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # CONSTRAINT_VIOLATION fires only when constraint_ok is False —
                    # signals downstream subsystems that the plan has a constraint breach.
                    [
                        "EMITIF",
                        ["constraint_ok", "==", False],
                        "CONSTRAINT_VIOLATION",
                        ["basket", "violations"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick after constraint check.
                    # Fires when constraint_ok is set (non-None) — regardless of value.
                    # Caller (EXECUTE, HYPOTHESIZE) branches on constraint_ok value.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    [
                        "FORKIF",
                        ["constraint_ok", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_CONSTRAIN"],
        "condition_signature": {
            "triggers": {"__entry__": "constrain_cell"},
            "basket_reads": ["sub_goals", "risk_signals"],
            "basket_writes": ["constraint_ok", "violations"],
        },
        "invariants": [
            "basket.constraint_ok must be set (bool) after execution (unless sub_goals was absent)",
            "basket.violations must be a list after execution (empty = no violations)",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null sub_goals — no partial writes",
            "FORKIF fires when constraint_ok is set AND next_node slot was provided at expansion time",
            "FORKIF fires regardless of constraint_ok value — caller branches on the bool",
            "constraint_ok=True + violations=[] is the valid optimistic scaffold default",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "CONSTRAIN is used in two contexts: "
            "(1) Basic plan chain: DECOMPOSE → CONSTRAIN → EXECUTE. "
            "Set CONSTRAIN's next_node slot to the EXECUTE node ID. "
            "(2) Debug loop: HYPOTHESIZE → CONSTRAIN → (branch: ok → EXECUTE, fail → HYPOTHESIZE). "
            "The FORKIF spawns the next cursor regardless of constraint_ok value; "
            "the receiving node reads basket.constraint_ok to branch. "
            "CONSTRAIN checks against cortex-loaded inertia levels, scope boundaries, "
            "and architecture patterns loaded by the prior SITUATE brick."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "CONSTRAIN — Layer 3 cognitive planning brick (D297). "
        "Template: given sub_goals + risk_signals in basket, checks the plan against "
        "known constraints (inertia levels, scope boundaries, architecture patterns). "
        "Emits constraint_ok (bool) + violations (list). Scaffold only — code_ref slot "
        "supplies the actual constraint-checking tool. Chains to next_node via FORKIF "
        "regardless of pass/fail (caller branches on constraint_ok value). "
        "Used in both the basic plan chain (after DECOMPOSE) and the debug loop "
        "(after HYPOTHESIZE)."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-constrain: CONSTRAIN Layer 3 planning brick. "
        "D297 Layer 3 standard library."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "CONSTRAIN",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer3",
            "planning",
            "constrain",
            "basket_contract",
            "constraint_check",
            "inertia",
        ],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). CONSTRAIN is the guard brick: check the "
            "decomposed plan against architecture constraints before execution. "
            "Decouples 'is this plan safe?' from 'what steps does the plan have?'. "
            "constraint_ok bool + violations list give downstream bricks a structured signal: "
            "proceed, retry (HYPOTHESIZE), or escalate. Dual-use: basic plan chain and "
            "debug loop both need the same constraint-check primitive."
        ),
    },
}


# ── Seed function ─────────────────────────────────────────────────────────────


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
    print()
    print("Verify with:")
    print(f"  Igor: memory_get('{TEMPLATE_ID}')")
    print("  Igor: list_templates()")
    print()
    print("To instantiate (example):")
    print(
        "  Igor: instantiate_template('tpl-layer3-constrain', "
        '\'{"prefix": "MAIN", "code_ref": "ops:check_constraints"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
