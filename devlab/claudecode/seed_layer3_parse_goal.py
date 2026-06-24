#!/usr/bin/env python3
"""
seed_layer3_parse_goal.py — PARSE_GOAL Layer 3 cognitive TEMPLATE node (D297/D298).

PARSE_GOAL is the first brick in Igor's cognitive planning chain (engram_language.md §10).
Given `user_input` in the basket, it extracts the actual goal/intent (distinct from
literal surface meaning) and emits `parsed_goal` and `parse_confidence` back.

Basket contract:
  Input:  user_input (str)
  Output: parsed_goal (str), parse_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual parsing tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:<password>@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_parse_goal.py

Verify:
    Igor: memory_get("tpl-layer3-parse-goal")
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

TEMPLATE_ID = "tpl-layer3-parse-goal"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix     (str, required)  — habit ID namespace, e.g. "MYBOT"
#                                 → produces MYBOT_PARSE_GOAL
#   code_ref   (str, required)  — tool fn to call for extraction,
#                                 e.g. "ops:parse_intent" or "prim_node_search"
#   next_node  (str, optional)  — node ID to FORKIF to after emitting
#                                 (default: null — no fork)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the parse-goal executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if user_input is absent (guard)
#     2. EMITIFs the code_ref output to basket.parsed_goal (via basket channel)
#        and basket.parse_confidence
#     3. FORKIFs to next_node if provided
#
# Note: real extraction happens inside the code_ref tool. The template wires the
# basket contract; the instantiator provides the tool. This keeps the scaffold
# decoupled from any particular NLP implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["user_input", "==", null]]    -- guard: no input, no-op
#   EMITIF  [True, "parsed_goal",  ["basket", "user_input"], "basket"]
#            ^^ placeholder: real impl calls code_ref and writes result
#   EMITIF  [True, "parse_confidence", 0.5, "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["parsed_goal", "!=", null], "{{ next_node }}"]
#            ^^ fork if goal extracted; target baked at expansion time (None → no-op)
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "PARSE_GOAL",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: extract actual goal/intent from user_input. "
        "Wires basket contract (user_input → parsed_goal + parse_confidence). "
        "code_ref slot is the extraction tool — pluggable, not hardcoded. "
        "FORKIF next_node chains to next planning brick (SITUATE, DECOMPOSE, etc.)."
    ),
    "basket_contract": {
        "reads": ["user_input"],
        "writes": ["parsed_goal", "parse_confidence"],
        "side_effects": ["FORKIF next_node if slot provided"],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_PARSE_GOAL.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for goal extraction, e.g. 'ops:parse_intent'. "
                "Must accept user_input from basket and return (parsed_goal, confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting parsed_goal. "
                "Null = standalone (no chaining). "
                "Set to SITUATE node ID for full planning-chain composition."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.5,
            "type_hint": "float",
            "description": "Fallback parse_confidence when code_ref does not emit one.",
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_PARSE_GOAL
            "id": "{{ prefix }}_PARSE_GOAL",
            "narrative": (
                "PARSE_GOAL: extract actual goal/intent from user_input. "
                "Reads basket.user_input → calls {{ code_ref }} → "
                "emits basket.parsed_goal + basket.parse_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-parse-goal (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-parse-goal",
                "layer": 3,
                "basket_reads": ["user_input"],
                "basket_writes": ["parsed_goal", "parse_confidence"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "parse_goal_cell"
                },
                "inertia": 0.3,
                "why": (
                    "First brick in the Layer 3 cognitive planning chain (D297/D298). "
                    "Isolates goal extraction from all downstream planning steps. "
                    "Allows any parsing tool to be plugged in without changing chain topology."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "PARSE_GOAL node — extract goal/intent from user_input. "
                    "Basket contract: reads user_input, writes parsed_goal + parse_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against empty input (null/missing user_input).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First two EMITIFs copy user_input → parsed_goal (placeholder).
                #     Real extraction happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the NLP.
                #   - Third EMITIF writes default_confidence from payload slot.
                #   - FORKIF forks to next_node if one was provided (non-None).
                #     "!= None" (string) — eval_gate handles None/null comparison.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "parse_goal_cell": [
                    # Guard: stop if user_input is absent or null
                    ["STOPIF", ["user_input", "==", None]],
                    # Emit parsed_goal from basket.user_input
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that returns the actual parsed
                    # intent. The scaffold uses basket pass-through as a valid default
                    # (identity extraction — parsed_goal = user_input verbatim).
                    ["EMITIF", True, "parsed_goal", ["basket", "user_input"], "basket"],
                    # Emit default parse_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "parse_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Push parsed_goal into TWM as ACTIVE_GOAL singleton (T-twm-goal-slot).
                    # CognitiveMilieuChannel evicts any prior active_goal before inserting,
                    # so TWM always holds exactly one ACTIVE_GOAL (or none). TTL=300s.
                    [
                        "EMITIF",
                        True,
                        "ACTIVE_GOAL",
                        ["basket", "parsed_goal"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if goal was successfully extracted
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    [
                        "FORKIF",
                        ["parsed_goal", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_PARSE_GOAL"],
        "condition_signature": {
            "triggers": {"__entry__": "parse_goal_cell"},
            "basket_reads": ["user_input"],
            "basket_writes": ["parsed_goal", "parse_confidence"],
        },
        "invariants": [
            "basket.parsed_goal must be set after execution (unless user_input was absent)",
            "basket.parse_confidence must be 0.0–1.0 float",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null user_input — no partial writes",
            "FORKIF fires when parsed_goal is set AND next_node slot was provided at expansion time",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain PARSE_GOAL → SITUATE by setting next_node slot to the SITUATE node ID. "
            "The FORKIF spawns SITUATE as a parallel cursor; basket is shared. "
            "SITUATE should read basket.parsed_goal as its input."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "PARSE_GOAL — Layer 3 cognitive planning brick (D297). "
        "Template: given user_input in basket, extracts actual goal/intent and "
        "emits parsed_goal + parse_confidence. Scaffold only — code_ref slot "
        "supplies the actual extraction tool. Chains to next_node via FORKIF."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-parse-goal: first Layer 3 planning brick. "
        "D297 Layer 3 standard library. D298 HYPOTHESIZE split."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "PARSE_GOAL",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer3", "planning", "parse_goal", "basket_contract"],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). PARSE_GOAL is brick #1: isolate goal extraction "
            "from all downstream planning steps. Decouples 'what did the user mean?' from "
            "'what do I do about it?'. Confidence threading: parse_confidence flows downstream "
            "so CONSTRAIN/HYPOTHESIZE can branch on certainty."
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
        "  Igor: instantiate_template('tpl-layer3-parse-goal', "
        '\'{"prefix": "MAIN", "code_ref": "ops:parse_intent"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
