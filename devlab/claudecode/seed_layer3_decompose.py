#!/usr/bin/env python3
"""
seed_layer3_decompose.py — DECOMPOSE Layer 3 cognitive TEMPLATE node (D297/D298).

DECOMPOSE is the third brick in Igor's cognitive planning chain (engram_language.md §10).
Given `parsed_goal` in the basket (output of PARSE_GOAL or updated by REPLAN), it breaks
the goal into an ordered list of sub-goals plus a dependency map. Emits `sub_goals`
(list), `dependency_map` (dict), and `decompose_confidence` (float) back to basket.

Re-entrant: can be called again after OBSERVE/REPLAN updates the basket with a revised
parsed_goal. Each re-entry overwrites sub_goals and dependency_map.

Basket contract:
  Input:  parsed_goal (str)
  Output: sub_goals (list), dependency_map (dict), decompose_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual decomposition tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:<password>@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_decompose.py

Verify:
    Igor: memory_get("tpl-layer3-decompose")
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

TEMPLATE_ID = "tpl-layer3-decompose"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix              (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                           → produces MYBOT_DECOMPOSE
#   code_ref            (str, required)   — tool fn to call for decomposition,
#                                           e.g. "ops:decompose_goal" or "prim_decompose"
#   next_node           (str, optional)   — node ID to FORKIF to after emitting
#                                           (default: null — no fork)
#   default_confidence  (float, optional) — fallback decompose_confidence when code_ref
#                                           does not emit one (default: 0.6)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the decompose executor.
#   The habit's payload has a single cell ("decompose_cell") that:
#     1. STOPIFs if parsed_goal is absent (guard)
#     2. EMITIFs [] to basket.sub_goals (scaffold default — empty list)
#        Real impl calls code_ref which returns the actual sub-goal list.
#     3. EMITIFs {} to basket.dependency_map (scaffold default — empty dict)
#        Real impl calls code_ref which returns the actual dependency map.
#     4. EMITIFs default_confidence to basket.decompose_confidence
#     5. FORKIFs to next_node if decompose_confidence is set and next_node was provided
#
# Note: real decomposition happens inside the code_ref tool. The template wires the
# basket contract; the instantiator provides the tool. This keeps the scaffold
# decoupled from any particular decomposition implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["parsed_goal", "==", null]]       -- guard: no input, no-op
#   EMITIF  [True, "sub_goals", [], "basket"]
#            ^^ placeholder: real impl calls code_ref and writes list result
#   EMITIF  [True, "dependency_map", {}, "basket"]
#            ^^ placeholder: real impl calls code_ref and writes dict result
#   EMITIF  [True, "decompose_confidence", ["payload", "default_confidence"], "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["decompose_confidence", "!=", None], "{{ next_node }}"]
#            ^^ fork if we produced a decomposition; target baked at expansion time
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "DECOMPOSE",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: break parsed_goal into ordered sub-goals + dependency map. "
        "Wires basket contract (parsed_goal → sub_goals + dependency_map + decompose_confidence). "
        "code_ref slot is the decomposition tool — pluggable, not hardcoded. "
        "FORKIF next_node chains to next planning brick (CONSTRAIN, HYPOTHESIZE, etc.). "
        "Re-entrant: fires again after REPLAN updates parsed_goal in basket."
    ),
    "basket_contract": {
        "reads": ["parsed_goal"],
        "writes": ["sub_goals", "dependency_map", "decompose_confidence"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "overwrites sub_goals/dependency_map on re-entry",
            "writes PLAN_READY=sub_goals to cognitive_milieu (TWM inter-subsystem channel, D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_DECOMPOSE.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for goal decomposition, e.g. 'ops:decompose_goal'. "
                "Must accept parsed_goal from basket and return "
                "(sub_goals, dependency_map, confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting sub_goals. "
                "Null = standalone (no chaining). "
                "Set to CONSTRAIN or HYPOTHESIZE node ID for full planning-chain composition."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.6,
            "type_hint": "float",
            "description": (
                "Fallback decompose_confidence when code_ref does not emit one. "
                "Default 0.6 — decomposition is moderately certain as a scaffold."
            ),
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_DECOMPOSE
            "id": "{{ prefix }}_DECOMPOSE",
            "narrative": (
                "DECOMPOSE: break parsed_goal into ordered sub-goals + dependency map. "
                "Reads basket.parsed_goal → calls {{ code_ref }} → "
                "emits basket.sub_goals + basket.dependency_map + basket.decompose_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-decompose (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-decompose",
                "layer": 3,
                "basket_reads": ["parsed_goal"],
                "basket_writes": [
                    "sub_goals",
                    "dependency_map",
                    "decompose_confidence",
                ],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "decompose_cell"
                },
                "inertia": 0.3,
                "why": (
                    "Third brick in the Layer 3 cognitive planning chain (D297/D298). "
                    "Isolates goal decomposition from context retrieval and downstream planning. "
                    "Re-entrant by design: REPLAN updates parsed_goal in basket and re-fires "
                    "DECOMPOSE to regenerate sub_goals/dependency_map without rebuilding the chain. "
                    "Allows any decomposition tool to be plugged in without changing chain topology."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "DECOMPOSE node — break parsed_goal into ordered sub-goals + dependency map. "
                    "Basket contract: reads parsed_goal, writes sub_goals + dependency_map "
                    "+ decompose_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against empty input (null/missing parsed_goal).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF writes sub_goals = [] (scaffold pass-through default).
                #     Real decomposition happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the decomposition logic.
                #     [] is the valid empty-list scaffold (no sub-goals identified yet).
                #   - Second EMITIF writes dependency_map = {} (scaffold empty-dict default).
                #     Real impl fills this with {sub_goal_id: [dependency_ids]}.
                #   - Third EMITIF writes default_confidence from payload slot.
                #   - FORKIF forks to next_node if decompose_confidence is set (non-None).
                #     "!= None" — eval_gate handles None/null comparison.
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "decompose_cell": [
                    # Guard: stop if parsed_goal is absent or null
                    ["STOPIF", ["parsed_goal", "==", None]],
                    # Emit sub_goals = [] (scaffold default: empty list)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that performs the actual goal
                    # decomposition and returns the real sub-goal list. The scaffold
                    # uses [] as the empty-list default — real code_ref fills it.
                    ["EMITIF", True, "sub_goals", [], "basket"],
                    # Emit dependency_map = {} (scaffold default: empty dict)
                    # Real code_ref fills this with {sub_goal_id: [dep_ids]}.
                    ["EMITIF", True, "dependency_map", {}, "basket"],
                    # Emit default decompose_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "decompose_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # PLAN_READY carries sub_goals so downstream subsystems can observe
                    # the current decomposition without basket access.
                    [
                        "EMITIF",
                        True,
                        "PLAN_READY",
                        ["basket", "sub_goals"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if decomposition was produced
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    [
                        "FORKIF",
                        ["decompose_confidence", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_DECOMPOSE"],
        "condition_signature": {
            "triggers": {"__entry__": "decompose_cell"},
            "basket_reads": ["parsed_goal"],
            "basket_writes": ["sub_goals", "dependency_map", "decompose_confidence"],
        },
        "invariants": [
            "basket.sub_goals must be set (list) after execution (unless parsed_goal was absent)",
            "basket.dependency_map must be set (dict) after execution (unless parsed_goal was absent)",
            "basket.decompose_confidence must be 0.0–1.0 float",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null parsed_goal — no partial writes",
            "FORKIF fires when decompose_confidence is set AND next_node slot was provided at expansion time",
            "Re-entrant: re-firing after REPLAN overwrites sub_goals and dependency_map",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain SITUATE → DECOMPOSE by setting SITUATE's next_node slot to the DECOMPOSE node ID. "
            "Chain DECOMPOSE → CONSTRAIN (or HYPOTHESIZE) by setting next_node slot accordingly. "
            "The FORKIF spawns the next cursor; basket is shared across the chain. "
            "DECOMPOSE is re-entrant: REPLAN can update basket.parsed_goal and re-fire DECOMPOSE "
            "to regenerate sub_goals/dependency_map without rebuilding the full chain."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "DECOMPOSE — Layer 3 cognitive planning brick (D297). "
        "Template: given parsed_goal in basket, breaks it into an ordered list of "
        "sub-goals plus a dependency map. Emits sub_goals (list) + dependency_map (dict) "
        "+ decompose_confidence. Scaffold only — code_ref slot supplies the actual "
        "decomposition tool. Re-entrant after REPLAN. Chains to next_node via FORKIF."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-decompose: third Layer 3 planning brick. "
        "D297 Layer 3 standard library. D298 HYPOTHESIZE split."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "DECOMPOSE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer3",
            "planning",
            "decompose",
            "basket_contract",
            "sub_goals",
            "reentrant",
        ],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). DECOMPOSE is brick #3: isolate goal decomposition "
            "from context retrieval (SITUATE) and downstream constraint/hypothesis generation. "
            "Re-entrance is a first-class design property: REPLAN updates parsed_goal and "
            "re-fires DECOMPOSE to refresh sub_goals/dependency_map without chain surgery. "
            "sub_goals and dependency_map flow downstream so CONSTRAIN/HYPOTHESIZE can branch "
            "on plan structure. decompose_confidence threads through so downstream bricks can "
            "weight their own reasoning by decomposition quality."
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
        "  Igor: instantiate_template('tpl-layer3-decompose', "
        '\'{"prefix": "MAIN", "code_ref": "ops:decompose_goal"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
