#!/usr/bin/env python3
"""
seed_layer3_hypothesize.py — HYPOTHESIZE Layer 3 cognitive TEMPLATE node (D297/D298).

HYPOTHESIZE is the universal predictive-coding primitive (D298). Given `delta` (the
observed discrepancy from OBSERVE) and `twm_loaded` context, it forms a candidate
causal explanation. The `time_direction` basket key controls temporal orientation:

  time_direction=backward (default) → abductive reasoning: explain how delta arose
  time_direction=forward            → anticipatory reasoning: predict risks/outcomes
                                       (formerly ANTICIPATE, collapsed per D298)

The brain does not distinguish predicting from explaining — both are hypothesis
formation over a model of the world. HYPOTHESIZE unifies them into a single primitive.

Basket contract:
  Input:  delta (str), twm_loaded (bool), time_direction (str: "forward"|"backward")
  Output: hypothesis (str), hypothesis_confidence (float 0.0–1.0)

The template defines the scaffold — opcode skeleton + basket contract + code_ref slot.
The instantiator supplies the actual hypothesize tool (code_ref slot).

Usage:
    cd ~/TheIgors && source venv/bin/activate
    IGOR_HOME_DB_URL=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_hypothesize.py

Verify:
    Igor: memory_get("tpl-layer3-hypothesize")
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

TEMPLATE_ID = "tpl-layer3-hypothesize"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix              (str, required)   — habit ID namespace, e.g. "MYBOT"
#                                           → produces MYBOT_HYPOTHESIZE
#   code_ref            (str, required)   — tool fn to call for hypothesis generation,
#                                           e.g. "ops:hypothesize" or "prim_hypothesize"
#   next_node           (str, optional)   — node ID to FORKIF to after emitting
#                                           (default: null — no fork)
#   default_confidence  (float, optional) — fallback hypothesis_confidence when code_ref
#                                           does not emit one (default: 0.6)
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the hypothesize executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if delta is absent or null (guard — delta is the required signal)
#     2. EMITIFs basket.delta → hypothesis (scaffold identity default)
#        Real impl calls code_ref which generates the actual causal explanation.
#     3. EMITIFs default_confidence to basket.hypothesis_confidence
#     4. FORKIFs to next_node if hypothesis is set and next_node was provided
#
# Note: real hypothesis generation happens inside the code_ref tool. The template
# wires the basket contract; the instantiator provides the tool. time_direction is
# declared as a basket_read so the code_ref tool can branch on it — the scaffold
# itself does not branch (that's the code_ref's responsibility).
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["delta", "==", null]]                    -- guard: no delta, no-op
#   EMITIF  [True, "hypothesis", ["basket", "delta"], "basket"]
#            ^^ placeholder: real impl calls code_ref and writes causal explanation
#   EMITIF  [True, "hypothesis_confidence", ["payload", "default_confidence"], "basket"]
#            ^^ default confidence; code_ref override expected at instantiation
#   FORKIF  [["hypothesis", "!=", None], "{{ next_node }}"]
#            ^^ fork if hypothesis formed; target baked at expansion time (None → no-op)
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "HYPOTHESIZE",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: form a candidate causal explanation given delta + TWM context. "
        "Wires basket contract (delta + twm_loaded + time_direction → hypothesis + hypothesis_confidence). "
        "code_ref slot is the hypothesis generation tool — pluggable, not hardcoded. "
        "time_direction basket key controls orientation (forward=anticipate, backward=explain). "
        "FORKIF next_node chains to next planning brick (CONSTRAIN, REPLAN, etc.)."
    ),
    "basket_contract": {
        "reads": ["delta", "twm_loaded", "time_direction"],
        "writes": ["hypothesis", "hypothesis_confidence"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "writes HYPOTHESIS=hypothesis to cognitive_milieu (TWM inter-subsystem channel, D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_HYPOTHESIZE.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for hypothesis generation, e.g. 'ops:hypothesize'. "
                "Must accept delta + twm_loaded + time_direction from basket and "
                "return (hypothesis, confidence)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting hypothesis. "
                "Null = standalone (no chaining). "
                "Set to CONSTRAIN node ID for debug loop composition, or REPLAN for replanning."
            ),
        },
        {
            "name": "default_confidence",
            "required": False,
            "default": 0.6,
            "type_hint": "float",
            "description": (
                "Fallback hypothesis_confidence when code_ref does not emit one. "
                "Default 0.6 — hypothesis formation is moderate certainty by nature."
            ),
            "validator": {"min": 0.0, "max": 1.0},
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_HYPOTHESIZE
            "id": "{{ prefix }}_HYPOTHESIZE",
            "narrative": (
                "HYPOTHESIZE: form a candidate causal explanation given delta + TWM context. "
                "Reads basket.delta + basket.twm_loaded + basket.time_direction → calls {{ code_ref }} → "
                "emits basket.hypothesis + basket.hypothesis_confidence. "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-hypothesize (D297/D298). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-hypothesize",
                "layer": 3,
                "basket_reads": ["delta", "twm_loaded", "time_direction"],
                "basket_writes": ["hypothesis", "hypothesis_confidence"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "hypothesize_cell"
                },
                "inertia": 0.3,
                "why": (
                    "D298: HYPOTHESIZE collapses ANTICIPATE into a single predictive-coding primitive. "
                    "The brain does not distinguish predicting from explaining — both are hypothesis "
                    "formation over a generative model of the world. time_direction (forward/backward) "
                    "is the only differentiator; the same opcode skeleton handles both. "
                    "Isolates hypothesis generation from observation (OBSERVE) and constraint checking "
                    "(CONSTRAIN), allowing any inference tool to be plugged in."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "HYPOTHESIZE node — form a candidate causal explanation given delta + TWM context. "
                    "Basket contract: reads delta + twm_loaded + time_direction, "
                    "writes hypothesis + hypothesis_confidence."
                ),
                "code_ref": "{{ code_ref }}",
                "default_confidence": "{{ default_confidence }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - STOPIF guards against missing delta (the required signal).
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF passes delta → hypothesis (scaffold identity default).
                #     Real hypothesis generation happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the inference logic.
                #     delta pass-through is a valid scaffold default (identity hypothesis).
                #   - Second EMITIF writes default_confidence from payload slot.
                #   - FORKIF forks to next_node if hypothesis is set (non-None).
                #     "{{ next_node }}" is baked at expansion time (None → "None" → no-op).
                #     The code_ref tool reads time_direction from basket to control orientation;
                #     the scaffold does not branch — that's the code_ref's responsibility.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "hypothesize_cell": [
                    # Guard: stop if delta is absent or null — delta is the required signal
                    ["STOPIF", ["delta", "==", None]],
                    # Emit hypothesis from basket.delta (scaffold identity default)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that performs the actual hypothesis
                    # generation (abductive backward or anticipatory forward). The scaffold
                    # uses basket pass-through as the valid default (hypothesis = delta verbatim).
                    # The code_ref tool reads basket.time_direction to control orientation.
                    ["EMITIF", True, "hypothesis", ["basket", "delta"], "basket"],
                    # Emit default hypothesis_confidence from payload slot
                    [
                        "EMITIF",
                        True,
                        "hypothesis_confidence",
                        ["payload", "default_confidence"],
                        "basket",
                    ],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # HYPOTHESIS carries the causal explanation so downstream subsystems
                    # can observe it from TWM without needing basket access.
                    [
                        "EMITIF",
                        True,
                        "HYPOTHESIS",
                        ["basket", "hypothesis"],
                        "cognitive_milieu",
                    ],
                    # Fork to next planning brick if hypothesis was formed
                    # AND a next_node was provided at expansion time.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    [
                        "FORKIF",
                        ["hypothesis", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_HYPOTHESIZE"],
        "condition_signature": {
            "triggers": {"__entry__": "hypothesize_cell"},
            "basket_reads": ["delta", "twm_loaded", "time_direction"],
            "basket_writes": ["hypothesis", "hypothesis_confidence"],
        },
        "invariants": [
            "basket.hypothesis must be set after execution (unless delta was absent)",
            "basket.hypothesis_confidence must be 0.0–1.0 float",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null delta — no partial writes",
            "FORKIF fires when hypothesis is set AND next_node slot was provided at expansion time",
            "time_direction basket key is the code_ref's responsibility — scaffold does not branch on it",
            "basket.twm_loaded and basket.time_direction must be declared as basket_reads even if "
            "the scaffold does not branch on them — the code_ref tool reads them at runtime",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "Used in debug loop: OBSERVE → HYPOTHESIZE → CONSTRAIN → REPLAN. "
            "Set next_node to CONSTRAIN node ID for debug loop composition. "
            "Used in risk scan: DECOMPOSE → HYPOTHESIZE(forward) → CONSTRAIN. "
            "Set time_direction='forward' in basket before calling for anticipatory mode. "
            "Default time_direction='backward' performs abductive (explain) reasoning."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "HYPOTHESIZE — Layer 3 cognitive planning brick (D297/D298). "
        "Universal predictive-coding primitive: given delta in basket, forms a candidate "
        "causal explanation (hypothesis). time_direction basket key controls orientation: "
        "backward=explain (abductive), forward=anticipate (predictive). "
        "D298: collapses ANTICIPATE into HYPOTHESIZE — same primitive, two temporal orientations. "
        "Scaffold only — code_ref slot supplies the actual inference tool. "
        "Chains to next_node via FORKIF."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-hypothesize: universal predictive-coding brick. "
        "D297 Layer 3 standard library. D298: ANTICIPATE collapsed into HYPOTHESIZE."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "HYPOTHESIZE",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer3",
            "planning",
            "hypothesize",
            "predictive_coding",
            "basket_contract",
            "d298",
        ],
        "inertia": 0.4,
        "why": (
            "D298: HYPOTHESIZE collapses ANTICIPATE into a single predictive-coding primitive. "
            "The brain does not distinguish predicting from explaining — predictive coding theory "
            "(Friston et al.) shows both use the same generative model: predicting forward and "
            "explaining backward are the same operation over a causal world-model. "
            "time_direction is the only differentiator. "
            "Used in: debug loop (OBSERVE → HYPOTHESIZE → CONSTRAIN → REPLAN) and "
            "risk scan (DECOMPOSE → HYPOTHESIZE(forward) → CONSTRAIN). "
            "hypothesis_confidence threads downstream so CONSTRAIN can branch on certainty."
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
        "  Igor: instantiate_template('tpl-layer3-hypothesize', "
        '\'{"prefix": "MAIN", "code_ref": "ops:hypothesize"}\')'
    )
    print()
    print("Debug loop composition:")
    print("  OBSERVE → HYPOTHESIZE(next_node=CONSTRAIN) → CONSTRAIN → REPLAN")
    print()
    print("Risk scan composition:")
    print("  DECOMPOSE → HYPOTHESIZE(forward, next_node=CONSTRAIN) → CONSTRAIN")


if __name__ == "__main__":
    seed(DB_URL)
