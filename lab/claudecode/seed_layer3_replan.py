#!/usr/bin/env python3
"""
seed_layer3_replan.py — REPLAN Layer 3 cognitive TEMPLATE node (D297).

REPLAN is the feedback loop that makes planning re-entrant. Given `delta`
(from OBSERVE) and the current `sub_goals` list (from DECOMPOSE), it updates
the decomposition and emits a revised `sub_goals` list and `replan_confidence`.

This is what makes debugging work: plan → observe → replan → act → observe → replan.
REPLAN overwrites basket.sub_goals — the revised plan replaces the old one.

Basket contract:
  Input:  delta (str), sub_goals (list)
  Output: sub_goals (list, updated/overwritten), replan_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual replanning tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_replan.py

Verify:
    Igor: memory_get("tpl-layer3-replan")
    Igor: list_templates()

Safe to re-run — upserts on conflict.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_URL = os.environ["IGOR_HOME_DB_URL"]

TEMPLATE_ID = "tpl-layer3-replan"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix              (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                           → produces MYBOT_REPLAN
#   code_ref            (str, required)   — tool fn to call for replanning,
#                                           e.g. "ops:replan_goals" or "prim_replan"
#   next_node           (str, optional)   — node ID to FORKIF to after emitting
#                                           (default: null — no fork; set to DECOMPOSE
#                                           node ID to form the re-entrant loop)
#   default_confidence  (float, optional) — fallback replan_confidence when code_ref
#                                           does not emit one (default: 0.6)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the replan executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if delta is absent (delta is the signal that replanning is needed)
#     2. EMITIFs the revised sub_goals to basket.sub_goals (overwrite, not append)
#        Real impl calls code_ref which computes updated sub_goals from delta + old sub_goals.
#     3. EMITIFs default_confidence to basket.replan_confidence
#     4. FORKIFs to next_node if replan_confidence is set (non-None) and next_node provided
#
# Note: real replanning happens inside the code_ref tool. The template wires the
# basket contract; the instantiator provides the tool. This keeps the scaffold
# decoupled from any particular planning implementation.
#
# Key design: REPLAN *overwrites* basket.sub_goals. This is intentional —
# the revised plan replaces the old one. Downstream nodes always see the
# current plan, never a stale one.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["delta", "==", null]]    -- guard: no delta, no replanning needed
#   EMITIF  [True, "sub_goals", ["basket", "sub_goals"], "basket"]
#            ^^ placeholder: real impl calls code_ref and writes revised sub_goals
#            ^^ pass-through default (sub_goals unchanged when code_ref not wired)
#   EMITIF  [True, "replan_confidence", ["payload", "default_confidence"], "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["replan_confidence", "!=", None], "{{ next_node }}"]
#            ^^ fork if replanning produced confidence; target baked at expansion time
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "REPLAN",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: update sub_goals decomposition given delta (observed gap). "
        "Wires basket contract (delta + sub_goals → sub_goals (updated) + replan_confidence). "
        "code_ref slot is the replanning tool — pluggable, not hardcoded. "
        "REPLAN *overwrites* basket.sub_goals — the revised plan replaces the old one. "
        "FORKIF next_node chains back to DECOMPOSE to form the re-entrant planning loop."
    ),
    "basket_contract": {
        "reads": ["delta", "sub_goals"],
        "writes": ["sub_goals", "replan_confidence"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "basket.sub_goals is overwritten (not appended)",
            "writes PLAN_READY=sub_goals to cognitive_milieu (TWM inter-subsystem channel, D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_REPLAN.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for replanning, e.g. 'ops:replan_goals'. "
                "Must accept delta + sub_goals from basket and return (updated_sub_goals, confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting revised sub_goals. "
                "Null = standalone (no chaining). "
                "Set to DECOMPOSE node ID to form the re-entrant planning loop: "
                "REPLAN → DECOMPOSE → ... → OBSERVE → REPLAN."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.6,
            "type_hint": "float",
            "description": (
                "Fallback replan_confidence when code_ref does not emit one. "
                "Default 0.6 — moderate certainty; replanning is inherently uncertain."
            ),
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_REPLAN
            "id": "{{ prefix }}_REPLAN",
            "narrative": (
                "REPLAN: update sub_goals decomposition given delta (observed gap). "
                "Reads basket.delta + basket.sub_goals → calls {{ code_ref }} → "
                "emits basket.sub_goals (updated) + basket.replan_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-replan (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-replan",
                "layer": 3,
                "basket_reads": ["delta", "sub_goals"],
                "basket_writes": ["sub_goals", "replan_confidence"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "replan_cell"
                },
                "inertia": 0.3,
                "why": (
                    "REPLAN is the feedback loop that makes planning re-entrant. "
                    "Without it, planning is front-loaded and brittle: a plan is made once "
                    "at the start and never updated when reality diverges. With REPLAN, "
                    "every observed delta feeds back into a revised sub_goals list. "
                    "This is what makes debugging work: plan → observe → replan → act → "
                    "observe → replan. The re-entrant loop is the key primitive."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "REPLAN node — update sub_goals given delta (observed gap). "
                    "Basket contract: reads delta + sub_goals, writes sub_goals (overwritten) "
                    "+ replan_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards on absent/null delta. delta is the signal that
                #     replanning is needed. Without a delta, REPLAN is a no-op —
                #     there is nothing to replan against. This prevents REPLAN from
                #     running spuriously when the pipeline hasn't observed anything.
                #   - First EMITIF writes revised sub_goals to basket.sub_goals.
                #     CRITICAL: this *overwrites* the existing sub_goals list.
                #     The pass-through default ["basket", "sub_goals"] preserves the
                #     current list when code_ref is not yet wired. Real impl replaces
                #     this with a code_ref call that computes the revised list.
                #   - Second EMITIF writes default_confidence from payload slot.
                #   - FORKIF forks to next_node if replan_confidence is non-None.
                #     Condition checks replan_confidence (not sub_goals) because
                #     replanning may produce an empty sub_goals list legitimately
                #     (goal achieved — nothing left to do). Confidence is always set.
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #     Set next_node=DECOMPOSE to form the re-entrant planning loop.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "replan_cell": [
                    # Guard: stop if delta is absent or null
                    # delta is the required trigger signal for replanning
                    ["STOPIF", ["delta", "==", None]],
                    # Emit revised sub_goals to basket (overwrites existing list)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that computes the revised
                    # sub_goals from delta + old sub_goals. The scaffold uses
                    # basket pass-through as a valid default (identity: sub_goals
                    # unchanged when code_ref not wired).
                    ["EMITIF", True, "sub_goals", ["basket", "sub_goals"], "basket"],
                    # Emit default replan_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "replan_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # PLAN_READY carries revised sub_goals — same key as DECOMPOSE uses,
                    # so replanning updates PLAN_READY in TWM (the current plan is always
                    # accessible to other subsystems via this key).
                    [
                        "EMITIF",
                        True,
                        "PLAN_READY",
                        ["basket", "sub_goals"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if replanning produced confidence
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    # Set next_node=DECOMPOSE to form the re-entrant planning loop.
                    [
                        "FORKIF",
                        ["replan_confidence", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_REPLAN"],
        "condition_signature": {
            "triggers": {"__entry__": "replan_cell"},
            "basket_reads": ["delta", "sub_goals"],
            "basket_writes": ["sub_goals", "replan_confidence"],
        },
        "invariants": [
            "basket.sub_goals must be set (list) after execution (unless delta was absent)",
            "basket.replan_confidence must be 0.0–1.0 float",
            "basket.sub_goals is *overwritten*, not appended — revised plan replaces old plan",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null delta — no partial writes",
            "FORKIF fires when replan_confidence is set AND next_node slot was provided at expansion time",
            "Empty sub_goals list is valid (goal achieved — nothing left to do)",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "REPLAN → DECOMPOSE forms the re-entrant loop. After replanning, pass "
            "next_node=DECOMPOSE to resume the planning chain with the updated sub_goals. "
            "Full cycle: DECOMPOSE → ... → OBSERVE → REPLAN → DECOMPOSE. "
            "Each REPLAN iteration refines the plan based on what was actually observed."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "REPLAN — Layer 3 cognitive planning brick (D297). "
        "Template: given delta (observed gap) and sub_goals in basket, updates the "
        "decomposition and emits revised sub_goals + replan_confidence. "
        "REPLAN *overwrites* basket.sub_goals — the revised plan replaces the old one. "
        "Scaffold only — code_ref slot supplies the actual replanning tool. "
        "Chains back to DECOMPOSE via FORKIF to form the re-entrant planning loop."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-replan: REPLAN Layer 3 planning brick — the re-entrant feedback loop. "
        "D297 Layer 3 standard library."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "REPLAN",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": ["layer3", "planning", "replan", "basket_contract", "feedback_loop"],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). REPLAN is the feedback loop brick: it makes "
            "planning re-entrant. Without REPLAN, planning is front-loaded and brittle — "
            "a plan is made once at the start and never updated when reality diverges. "
            "With REPLAN, every observed delta (gap between expected and actual) feeds back "
            "into a revised sub_goals list. REPLAN → DECOMPOSE forms the re-entrant loop "
            "that underlies all adaptive cognition: plan → observe → replan → act."
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
        "  Igor: instantiate_template('tpl-layer3-replan', "
        '\'{"prefix": "MAIN", "code_ref": "ops:replan_goals", "next_node": "MAIN_DECOMPOSE"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
