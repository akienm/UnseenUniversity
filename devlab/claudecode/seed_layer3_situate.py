#!/usr/bin/env python3
"""
seed_layer3_situate.py — SITUATE Layer 3 cognitive TEMPLATE node (D297/D298).

SITUATE is the second brick in Igor's cognitive planning chain (engram_language.md §10).
Given `parsed_goal` in the basket (output of PARSE_GOAL), it searches cortex for
relevant memories and loads them into TWM (working memory). Emits `twm_loaded`
(bool) and `situate_confidence` (float) back to the basket.

Basket contract:
  Input:  parsed_goal (str)
  Output: twm_loaded (bool), situate_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual cortex search tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_situate.py

Verify:
    Igor: memory_get("tpl-layer3-situate")
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

TEMPLATE_ID = "tpl-layer3-situate"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix              (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                           → produces MYBOT_SITUATE
#   code_ref            (str, required)   — tool fn to call for cortex search,
#                                           e.g. "ops:cortex_search" or "prim_cortex_load"
#   next_node           (str, optional)   — node ID to FORKIF to after emitting
#                                           (default: null — no fork)
#   default_confidence  (float, optional) — fallback situate_confidence when code_ref
#                                           does not emit one (default: 0.7 — retrieval
#                                           ops tend to succeed, higher baseline than
#                                           PARSE_GOAL's 0.5)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the situate executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if parsed_goal is absent (guard)
#     2. EMITIFs True to basket.twm_loaded (scaffold pass-through default)
#        Real impl calls code_ref which does cortex search and populates TWM.
#     3. EMITIFs default_confidence to basket.situate_confidence
#     4. FORKIFs to next_node if twm_loaded is set and next_node was provided
#
# Note: real cortex search happens inside the code_ref tool. The template wires
# the basket contract; the instantiator provides the tool. This keeps the scaffold
# decoupled from any particular retrieval implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["parsed_goal", "==", null]]       -- guard: no input, no-op
#   EMITIF  [True, "twm_loaded", True, "basket"]
#            ^^ placeholder: real impl calls code_ref and writes bool result
#   EMITIF  [True, "situate_confidence", ["payload", "default_confidence"], "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["twm_loaded", "!=", None], "{{ next_node }}"]
#            ^^ fork if context loaded; target baked at expansion time (None → no-op)
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "SITUATE",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: load relevant cortex context into TWM given parsed_goal. "
        "Wires basket contract (parsed_goal → twm_loaded + situate_confidence). "
        "code_ref slot is the cortex search tool — pluggable, not hardcoded. "
        "FORKIF next_node chains to next planning brick (DECOMPOSE, CONSTRAIN, etc.)."
    ),
    "basket_contract": {
        "reads": ["parsed_goal"],
        "writes": ["twm_loaded", "situate_confidence"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "populates TWM via code_ref",
            "writes CONTEXT_LOADED=True to cognitive_milieu (TWM inter-subsystem channel, D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_SITUATE.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for cortex search and TWM loading, e.g. 'ops:cortex_search'. "
                "Must accept parsed_goal from basket and return (twm_loaded, confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting twm_loaded. "
                "Null = standalone (no chaining). "
                "Set to DECOMPOSE node ID for full planning-chain composition."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.7,
            "type_hint": "float",
            "description": (
                "Fallback situate_confidence when code_ref does not emit one. "
                "Default 0.7 (higher than PARSE_GOAL's 0.5 — retrieval ops tend to succeed)."
            ),
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_SITUATE
            "id": "{{ prefix }}_SITUATE",
            "narrative": (
                "SITUATE: load relevant cortex context into TWM given parsed_goal. "
                "Reads basket.parsed_goal → calls {{ code_ref }} → "
                "emits basket.twm_loaded + basket.situate_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-situate (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-situate",
                "layer": 3,
                "basket_reads": ["parsed_goal"],
                "basket_writes": ["twm_loaded", "situate_confidence"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "situate_cell"
                },
                "inertia": 0.3,
                "why": (
                    "Second brick in the Layer 3 cognitive planning chain (D297/D298). "
                    "Isolates cortex retrieval from goal parsing and downstream decomposition. "
                    "Allows any cortex search tool to be plugged in without changing chain topology. "
                    "twm_loaded bool enables downstream nodes to branch on retrieval success."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "SITUATE node — load relevant cortex context into TWM given parsed_goal. "
                    "Basket contract: reads parsed_goal, writes twm_loaded + situate_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against empty input (null/missing parsed_goal).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF writes twm_loaded = True (scaffold pass-through default).
                #     Real cortex search happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the retrieval logic.
                #     True is emitted as a valid bool indicating context was loaded.
                #   - Second EMITIF writes default_confidence from payload slot.
                #   - FORKIF forks to next_node if twm_loaded is set (non-None).
                #     "!= None" — eval_gate handles None/null comparison.
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "situate_cell": [
                    # Guard: stop if parsed_goal is absent or null
                    ["STOPIF", ["parsed_goal", "==", None]],
                    # Emit twm_loaded = True (scaffold default: assume retrieval succeeded)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that performs the actual cortex
                    # search and returns the real bool result. The scaffold uses True
                    # as a pass-through default (identity: cortex loaded = True).
                    ["EMITIF", True, "twm_loaded", True, "basket"],
                    # Emit default situate_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "situate_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # cognitive_milieu channel evicts prior CONTEXT_LOADED before inserting,
                    # so TWM always holds exactly one CONTEXT_LOADED (or none). TTL=300s.
                    [
                        "EMITIF",
                        True,
                        "CONTEXT_LOADED",
                        True,
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if context was loaded
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    [
                        "FORKIF",
                        ["twm_loaded", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_SITUATE"],
        "condition_signature": {
            "triggers": {"__entry__": "situate_cell"},
            "basket_reads": ["parsed_goal"],
            "basket_writes": ["twm_loaded", "situate_confidence"],
        },
        "invariants": [
            "basket.twm_loaded must be set (bool) after execution (unless parsed_goal was absent)",
            "basket.situate_confidence must be 0.0–1.0 float",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null parsed_goal — no partial writes",
            "FORKIF fires when twm_loaded is set AND next_node slot was provided at expansion time",
            "twm_loaded=True indicates cortex search returned results; False indicates no results",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Chain PARSE_GOAL → SITUATE by setting PARSE_GOAL's next_node slot to the SITUATE node ID. "
            "Chain SITUATE → DECOMPOSE by setting next_node slot to the DECOMPOSE node ID. "
            "The FORKIF spawns the next cursor; basket is shared across the chain. "
            "DECOMPOSE should read basket.parsed_goal and basket.twm_loaded as its inputs."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "SITUATE — Layer 3 cognitive planning brick (D297). "
        "Template: given parsed_goal in basket, searches cortex for relevant memories "
        "and loads them into TWM. Emits twm_loaded (bool) + situate_confidence. "
        "Scaffold only — code_ref slot supplies the actual retrieval tool. "
        "Chains to next_node via FORKIF."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-situate: second Layer 3 planning brick. "
        "D297 Layer 3 standard library. D298 HYPOTHESIZE split."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "SITUATE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer3", "planning", "situate", "basket_contract", "twm", "cortex"],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). SITUATE is brick #2: load relevant context into "
            "TWM before decomposition. Decouples 'what context is relevant?' from 'what do I do "
            "about it?'. twm_loaded bool enables downstream bricks to branch on retrieval success. "
            "situate_confidence threads downstream so CONSTRAIN/HYPOTHESIZE can weight context quality."
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
        "  Igor: instantiate_template('tpl-layer3-situate', "
        '\'{"prefix": "MAIN", "code_ref": "ops:cortex_search"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
