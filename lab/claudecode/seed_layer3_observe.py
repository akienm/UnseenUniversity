#!/usr/bin/env python3
"""
seed_layer3_observe.py — OBSERVE Layer 3 cognitive TEMPLATE node (D297/D298).

OBSERVE is the prediction-error signal in Igor's cognitive loop (D279/D298
predictive coding). Given `expected` and `actual` in the basket, it computes
the difference (delta) and emits `delta` (the error/difference description)
and `observation_confidence` back. Triggers REPLAN/HYPOTHESIZE when delta is
significant.

Basket contract:
  Input:  actual (str or any — required), expected (str or any — optional)
  Output: delta (str describing the difference), observation_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual comparison/diff tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_observe.py

Verify:
    Igor: memory_get("tpl-layer3-observe")
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

TEMPLATE_ID = "tpl-layer3-observe"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix              (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                           → produces MYBOT_OBSERVE
#   code_ref            (str, required)   — tool fn to call for diff/comparison,
#                                           e.g. "ops:compute_delta" or "prim_observe"
#   next_node           (str, optional)   — node ID to FORKIF to after emitting
#                                           (default: null — no fork)
#                                           Caller decides: REPLAN (delta → update plan)
#                                           or HYPOTHESIZE (delta → causal explanation).
#   default_confidence  (float, optional) — fallback observation_confidence when
#                                           code_ref does not emit one (default: 0.8 —
#                                           observation is high-confidence by default;
#                                           seeing IS knowing)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the observe executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if actual is absent or null (actual is the required signal;
#        expected may be absent for pure observation — delta = actual verbatim)
#     2. EMITIFs actual → basket.delta (scaffold pass-through default;
#        real impl calls code_ref which computes the actual diff)
#     3. EMITIFs default_confidence to basket.observation_confidence
#     4. FORKIFs to next_node if delta is set (non-None)
#
# Note: real diff/comparison happens inside the code_ref tool. The template wires
# the basket contract; the instantiator provides the tool. This keeps the scaffold
# decoupled from any particular diff implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["actual", "==", null]]          -- guard: no actual signal, no-op
#   EMITIF  [True, "delta", ["basket", "actual"], "basket"]
#            ^^ placeholder: real impl calls code_ref and computes diff vs expected
#   EMITIF  [True, "observation_confidence", ["payload", "default_confidence"], "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["delta", "!=", None], "{{ next_node }}"]
#            ^^ fork after observation if delta was produced; caller picks REPLAN or HYPOTHESIZE
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "OBSERVE",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: prediction-error signal (D279/D298 predictive coding). "
        "Given actual (and optionally expected) in basket, computes delta (the difference/error) "
        "and emits delta + observation_confidence. "
        "code_ref slot is the comparison/diff tool — pluggable, not hardcoded. "
        "FORKIF next_node chains to REPLAN (delta → update plan) or HYPOTHESIZE "
        "(delta → causal explanation) — caller decides which via next_node slot."
    ),
    "basket_contract": {
        "reads": ["actual", "expected"],
        "writes": ["delta", "observation_confidence"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "writes DELTA=delta to cognitive_milieu (TWM inter-subsystem channel, D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_OBSERVE.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for diff/comparison, e.g. 'ops:compute_delta'. "
                "Must accept actual (and optionally expected) from basket and "
                "return (delta, observation_confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting delta. "
                "Null = standalone (no chaining). "
                "Set to REPLAN node ID to update the plan on prediction error, "
                "or HYPOTHESIZE node ID to generate a causal explanation."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.8,
            "type_hint": "float",
            "description": (
                "Fallback observation_confidence when code_ref does not emit one. "
                "Default 0.8 — observation is high-confidence by default (seeing IS knowing). "
                "Higher than PARSE_GOAL's 0.5 and SITUATE's 0.7."
            ),
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_OBSERVE
            "id": "{{ prefix }}_OBSERVE",
            "narrative": (
                "OBSERVE: compare expected vs actual and emit prediction-error signal. "
                "Reads basket.actual (+ basket.expected if present) → calls {{ code_ref }} → "
                "emits basket.delta + basket.observation_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-observe (D297/D298). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-observe",
                "layer": 3,
                "basket_reads": ["actual", "expected"],
                "basket_writes": ["delta", "observation_confidence"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "observe_cell"
                },
                "inertia": 0.3,
                "why": (
                    "OBSERVE is the prediction-error signal of D279/D298 predictive coding. "
                    "Part of the Layer 3 cognitive planning chain (D297/D298). "
                    "Isolates observation (expected vs actual diff) from all downstream "
                    "planning steps. Allows any diff/comparison tool to be plugged in "
                    "without changing chain topology. delta feeds REPLAN (update plan) "
                    "or HYPOTHESIZE (causal explanation) — the caller decides via next_node."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "OBSERVE node — prediction-error signal; compare expected vs actual. "
                    "Basket contract: reads actual (+ expected), writes delta + observation_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against missing actual signal (null/absent actual).
                #     actual is the required input — expected may be absent for pure
                #     observation (delta = actual verbatim in that case).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF copies actual → delta (scaffold pass-through default).
                #     Real diff computation happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the comparison logic.
                #     Pass-through means: delta = actual (no expected → error = signal itself).
                #   - Second EMITIF writes default_confidence from payload slot.
                #     0.8 default — observation is high-confidence (seeing IS knowing).
                #   - FORKIF forks to next_node if delta was produced (non-None).
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #     node_executor's FORKIF skips falsy/"None" targets, so this is safe.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "observe_cell": [
                    # Guard: stop if actual is absent or null
                    # actual is the required signal — expected may be absent (pure observation)
                    ["STOPIF", ["actual", "==", None]],
                    # Emit delta from basket.actual (scaffold default: pass-through)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that computes the actual diff
                    # between expected and actual. The scaffold uses basket pass-through
                    # as a valid default (identity: delta = actual verbatim — the
                    # observation IS the signal when no expected baseline is given).
                    ["EMITIF", True, "delta", ["basket", "actual"], "basket"],
                    # Emit default observation_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "observation_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # DELTA carries the observation delta so downstream subsystems
                    # (REPLAN, HYPOTHESIZE) can read it from TWM, not only from basket.
                    [
                        "EMITIF",
                        True,
                        "DELTA",
                        ["basket", "delta"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if delta was produced
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    # Caller decides: REPLAN (delta → update plan) or HYPOTHESIZE
                    # (delta → causal explanation).
                    [
                        "FORKIF",
                        ["delta", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_OBSERVE"],
        "condition_signature": {
            "triggers": {"__entry__": "observe_cell"},
            "basket_reads": ["actual", "expected"],
            "basket_writes": ["delta", "observation_confidence"],
        },
        "invariants": [
            "basket.delta must be set after execution (unless actual was absent)",
            "basket.observation_confidence must be 0.0–1.0 float",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null actual — no partial writes",
            "FORKIF fires when delta is set AND next_node slot was provided at expansion time",
            "expected may be absent — delta = actual verbatim is valid (pure observation)",
            "FORKIF target is caller-supplied: REPLAN or HYPOTHESIZE depending on use case",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "OBSERVE feeds into both REPLAN (delta → update plan) and HYPOTHESIZE "
            "(delta → causal explanation). The caller decides which to chain via next_node. "
            "Chain any upstream brick → OBSERVE by setting that brick's next_node to the "
            "OBSERVE node ID. Chain OBSERVE → REPLAN by setting next_node to the REPLAN "
            "node ID, or OBSERVE → HYPOTHESIZE for causal explanation. "
            "The FORKIF spawns the next cursor; basket is shared across the chain. "
            "REPLAN/HYPOTHESIZE should read basket.delta and basket.observation_confidence."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "OBSERVE — Layer 3 cognitive planning brick (D297/D298). "
        "Template: given actual (and optionally expected) in basket, computes "
        "the prediction error (delta) and emits delta + observation_confidence. "
        "This is the prediction-error signal of D279/D298 predictive coding. "
        "Scaffold only — code_ref slot supplies the actual diff/comparison tool. "
        "Chains to next_node (REPLAN or HYPOTHESIZE) via FORKIF."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-observe: OBSERVE Layer 3 planning brick. "
        "D297 Layer 3 standard library. D298 HYPOTHESIZE split. "
        "D279 predictive-coding gap deposit + escalation-stats monitoring."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "OBSERVE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer3",
            "planning",
            "observe",
            "basket_contract",
            "predictive_coding",
            "prediction_error",
        ],
        "inertia": 0.4,
        "why": (
            "Planning is not a single engram — it is the composition of smaller cognitive "
            "bricks (engram_language.md §10). OBSERVE is the prediction-error signal: "
            "the gap between what was expected and what actually happened. "
            "This is the core mechanism of D279/D298 predictive coding — the brain does not "
            "passively record; it predicts and updates on error. delta flows downstream so "
            "REPLAN (update the plan) or HYPOTHESIZE (explain the error causally) can act on it. "
            "observation_confidence threads downstream so confidence-weighted branching is possible."
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
        "  Igor: instantiate_template('tpl-layer3-observe', "
        '\'{"prefix": "MAIN", "code_ref": "ops:compute_delta"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
