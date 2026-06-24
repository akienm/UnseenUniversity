#!/usr/bin/env python3
"""
seed_layer3_scope_check.py — SCOPE_CHECK Layer 3 cognitive TEMPLATE node (D297).

SCOPE_CHECK is a continuous guard that prevents goal drift mid-execution.
Given `current_action` and `parsed_goal` in the basket, it verifies the action
is still solving the right problem. Emits `scope_ok` (bool) and `drift_signal`
(str describing any detected drift, or None) back to the basket.

Basket contract:
  Input:  current_action (str), parsed_goal (str)
  Output: scope_ok (bool), drift_signal (str or None)

Note from engram_language.md: "Scope guard: SCOPE_CHECK fires continuously
alongside execution" — it's not a one-shot planning step, it runs in parallel
with the execution loop.

Usage:
    cd ~/TheIgors && source venv/bin/activate
    UU_HOME_DB_URL=postgresql://igor:<password>@127.0.0.1/Igor-wild-0001 \\
        python claudecode/seed_layer3_scope_check.py

Verify:
    Igor: memory_get("tpl-layer3-scope-check")
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

TEMPLATE_ID = "tpl-layer3-scope-check"

# ── Template schema ───────────────────────────────────────────────────────────
#
# Slot manifest:
#   prefix     (str, required)  — habit ID namespace, e.g. "MYBOT"
#                                 → produces MYBOT_SCOPE_CHECK
#   code_ref   (str, required)  — tool fn to call for scope comparison,
#                                 e.g. "ops:check_scope" or "prim_scope_verify"
#   next_node  (str, optional)  — node ID to FORKIF to after emitting
#                                 (default: null — no fork; caller branches on scope_ok)
#
# No default_confidence slot — SCOPE_CHECK emits scope_ok bool, not a confidence
# float. The scope check is binary: in-scope or drifted.
#
# Expansion:
#   One PROCEDURAL habit per instantiation — the scope-check executor.
#   The habit's payload has a single cell ("__entry__") that:
#     1. STOPIFs if current_action is absent (guard)
#     2. STOPIFs if parsed_goal is absent (guard — both inputs required)
#     3. EMITIFs scope_ok = True (optimistic default)
#     4. EMITIFs drift_signal = None (no drift by default)
#     5. FORKIFs to next_node regardless of scope_ok result
#        (caller branches on scope_ok — FORKIF fires when scope_ok is set)
#
# Note: real scope comparison happens inside the code_ref tool. The template wires
# the basket contract; the instantiator provides the comparison tool. This keeps
# the scaffold decoupled from any particular NLP or heuristic implementation.
#
# Opcode skeleton (using node_executor instruction set from D260/D290/D291):
#
#   STOPIF  [["current_action", "==", null]]    -- guard: no action, no-op
#   STOPIF  [["parsed_goal", "==", null]]       -- guard: no goal, no-op
#   EMITIF  [True, "scope_ok", True, "basket"]
#            ^^ optimistic default: assume in-scope until code_ref says otherwise
#   EMITIF  [True, "drift_signal", None, "basket"]
#            ^^ default: no drift detected
#   FORKIF  [["scope_ok", "!=", None], "{{ next_node }}"]
#            ^^ fork after check regardless of scope_ok value;
#            caller node branches on basket.scope_ok
#
# The expansion_schema uses Jinja2 for slot substitution.

TEMPLATE_SCHEMA = {
    "pattern_name": "SCOPE_CHECK",
    "layer": 3,
    "schema_version": 1,
    "substitution_engine": "jinja2",
    "description": (
        "Layer 3 cognitive brick: continuous guard against goal drift mid-execution. "
        "Wires basket contract (current_action + parsed_goal → scope_ok + drift_signal). "
        "code_ref slot is the comparison tool — pluggable, not hardcoded. "
        "FORKIF next_node fires after check; caller branches on scope_ok bool. "
        "Fires in parallel with execution loop, not as a sequential planning step."
    ),
    "basket_contract": {
        "reads": ["current_action", "parsed_goal"],
        "writes": ["scope_ok", "drift_signal"],
        "side_effects": [
            "FORKIF next_node if slot provided",
            "writes SCOPE_DRIFT=drift_signal to cognitive_milieu when scope_ok is False (D300)",
        ],
    },
    "slot_manifest": [
        {
            "name": "prefix",
            "required": True,
            "type_hint": "str",
            "description": "Habit ID namespace. Produced habit = {{prefix}}_SCOPE_CHECK.",
            "validator": {"pattern": r"^[A-Z][A-Z0-9_]+$"},
        },
        {
            "name": "code_ref",
            "required": True,
            "type_hint": "str",
            "description": (
                "Tool fn used for scope comparison, e.g. 'ops:check_scope'. "
                "Must accept current_action and parsed_goal from basket and "
                "return (scope_ok: bool, drift_signal: str or None)."
            ),
        },
        {
            "name": "next_node",
            "required": False,
            "default": None,
            "type_hint": "str",
            "description": (
                "Optional node ID to FORKIF after emitting scope_ok + drift_signal. "
                "Null = standalone (no chaining). "
                "Typically set to an alert/replan node that branches on scope_ok=False."
            ),
        },
    ],
    "expansion_schema": [
        {
            # One PROCEDURAL habit produced per instantiation.
            # ID format: {{prefix}}_SCOPE_CHECK
            "id": "{{ prefix }}_SCOPE_CHECK",
            "narrative": (
                "SCOPE_CHECK: verify current_action still addresses parsed_goal. "
                "Reads basket.current_action + basket.parsed_goal → calls {{ code_ref }} → "
                "emits basket.scope_ok (bool) + basket.drift_signal (str or None). "
                "{% if next_node %}FORKs to {{ next_node }}.{% endif %}"
            ),
            "memory_type": "PROCEDURAL",
            "source": "template_expansion",
            "confidence": 1.0,
            "context_of_encoding": (
                "Expanded from tpl-layer3-scope-check (D297). "
                "prefix={{ prefix }} code_ref={{ code_ref }}"
            ),
            "metadata": {
                "habit_type": "cognitive",
                "template": False,
                "template_parent": "tpl-layer3-scope-check",
                "layer": 3,
                "basket_reads": ["current_action", "parsed_goal"],
                "basket_writes": ["scope_ok", "drift_signal"],
                "code_ref": "{{ code_ref }}",
                "triggers": {
                    # __entry__ is the canonical entry trigger for cursor traversal
                    "__entry__": "scope_check_cell"
                },
                "inertia": 0.3,
                "why": (
                    "Prevents the classic agentic failure mode where the agent solves "
                    "the wrong problem because intermediate steps drifted the effective goal. "
                    "Fires continuously alongside execution, not just at plan time. "
                    "scope_ok=False signals the execution loop to halt or replan rather than "
                    "continue toward the wrong target. Parallel guard — does not block the "
                    "execution path; caller branches on scope_ok after the FORKIF."
                ),
            },
            "payload": {
                # Non-cell data fields (embedding source, readable description)
                "NARRATIVE": (
                    "SCOPE_CHECK node — verify current_action still addresses parsed_goal. "
                    "Basket contract: reads current_action + parsed_goal, "
                    "writes scope_ok (bool) + drift_signal (str or None)."
                ),
                "code_ref": "{{ code_ref }}",
                # ── Executable cell ──────────────────────────────────────────
                # Instruction set: STOPIF, EMITIF, FORKIF (node_executor D260)
                #
                # Design notes:
                #   - Two STOPIF guards — one per required input.
                #     Both current_action and parsed_goal are mandatory; if either
                #     is absent the check cannot run and we no-op cleanly.
                #     A missing key evaluates to None via eval_gate; "==" None is True.
                #   - First EMITIF writes scope_ok = True (optimistic default).
                #     Real comparison happens in code_ref at instantiation time;
                #     the template wires the basket contract, not the heuristic.
                #   - Second EMITIF writes drift_signal = None (no drift by default).
                #   - FORKIF fires when scope_ok is set (non-None) — fires after both
                #     defaults are emitted, regardless of whether scope_ok is True or False.
                #     The fork target is an alert/replan node; the caller inspects scope_ok
                #     to decide what to do. Using ["scope_ok", "!=", None] rather than
                #     ["scope_ok", "==", True] ensures the fork fires even when code_ref
                #     writes scope_ok=False, so the alert node is always reached.
                #   - ENDIF — explicit terminator (good hygiene per D260).
                "scope_check_cell": [
                    # Guard: stop if current_action is absent or null
                    ["STOPIF", ["current_action", "==", None]],
                    # Guard: stop if parsed_goal is absent or null
                    ["STOPIF", ["parsed_goal", "==", None]],
                    # Emit scope_ok = True (optimistic default)
                    # NOTE: at instantiation time, the instantiator should replace
                    # this EMITIF with a code_ref call that performs the actual scope
                    # comparison and returns the real bool result.
                    ["EMITIF", True, "scope_ok", True, "basket"],
                    # Emit drift_signal = None (no drift detected by default)
                    # code_ref override expected at instantiation: returns a descriptive
                    # string when drift is detected, e.g. "action 'write DB schema' does
                    # not address goal 'answer user's question about Python syntax'"
                    ["EMITIF", True, "drift_signal", None, "basket"],
                    # Write durable output to TWM as inter-subsystem signal (D300).
                    # SCOPE_DRIFT fires only when scope_ok is False — signals downstream
                    # subsystems that the current action is drifting from the parsed_goal.
                    [
                        "EMITIF",
                        ["scope_ok", "==", False],
                        "SCOPE_DRIFT",
                        ["basket", "drift_signal"],
                        "cognitive_milieu",
                    ],
                    # Fork to alert/replan node after scope check completes.
                    # Target "{{ next_node }}" is baked in by Jinja2 at expansion time.
                    # When next_node slot is None (standalone use), Jinja2 renders "None"
                    # — node_executor's FORKIF skips falsy/None targets, so this is safe.
                    # Condition: ["scope_ok", "!=", None] — fires whenever scope_ok has
                    # been set (True or False), routing to the caller's branch logic.
                    [
                        "FORKIF",
                        ["scope_ok", "!=", None],
                        "{{ next_node }}",
                    ],
                    "ENDIF",
                ],
            },
        }
    ],
    "instantiation_contract": {
        "produces": ["{{ prefix }}_SCOPE_CHECK"],
        "condition_signature": {
            "triggers": {"__entry__": "scope_check_cell"},
            "basket_reads": ["current_action", "parsed_goal"],
            "basket_writes": ["scope_ok", "drift_signal"],
        },
        "invariants": [
            "basket.scope_ok must be bool after execution (unless either input was absent)",
            "basket.drift_signal must be str or None after execution",
            "code_ref must be registered in tool registry before instantiation",
            "STOPIF guard fires on absent/null current_action — no partial writes",
            "STOPIF guard fires on absent/null parsed_goal — no partial writes",
            "FORKIF fires when scope_ok is set (non-None) AND next_node slot was provided at expansion time",
            "scope_ok=True means action is on-scope; scope_ok=False signals drift — caller must branch",
        ],
        "edge_policy": "link_to_parent",
        "chaining_note": (
            "SCOPE_CHECK is a parallel guard, not a sequential step. "
            "It can FORKIF to an alert/replan node when scope_ok=False. "
            "Typical wiring: execution loop fires SCOPE_CHECK alongside each action node; "
            "FORKIF next_node points to an ALERT_DRIFT or REPLAN node that inspects "
            "basket.scope_ok and basket.drift_signal to decide whether to halt or re-route. "
            "Do not place SCOPE_CHECK in a sequential chain — it should run in parallel "
            "via the cursor spawning mechanism so it does not block the main execution path."
        ),
    },
}

# ── Memory node (the TEMPLATE itself, stored in Postgres) ────────────────────

TEMPLATE_NODE = {
    "id": TEMPLATE_ID,
    "narrative": (
        "SCOPE_CHECK — Layer 3 cognitive planning brick (D297). "
        "Template: given current_action and parsed_goal in basket, verifies the action "
        "still addresses the original goal. Emits scope_ok (bool) + drift_signal (str or None). "
        "Scaffold only — code_ref slot supplies the actual comparison tool. "
        "Fires continuously alongside execution as a parallel guard."
    ),
    "memory_type": "PROCEDURAL",
    "source": "user_seeded",
    "confidence": 1.0,
    "context_of_encoding": (
        "T-layer3-scope-check: continuous scope guard brick. "
        "D297 Layer 3 standard library. Prevents goal drift mid-execution."
    ),
    "metadata": {
        "template": True,  # BG executor guard: never fire this node directly
        "schema_version": 1,
        "layer": 3,
        "pattern_name": "SCOPE_CHECK",
        "template_schema": TEMPLATE_SCHEMA,
        "tags": [
            "layer3",
            "planning",
            "scope_check",
            "basket_contract",
            "guard",
            "parallel",
        ],
        "inertia": 0.4,
        "why": (
            "The canonical agentic failure mode is solving the wrong problem: intermediate steps "
            "drift the effective goal and the agent never notices. SCOPE_CHECK is the continuous "
            "guard that catches this. Unlike sequential planning bricks (PARSE_GOAL, SITUATE), "
            "SCOPE_CHECK fires in parallel with execution — it is woven into the execution loop, "
            "not placed before it. scope_ok=False is the signal to halt or replan. "
            "drift_signal carries a human-readable description of the detected drift for "
            "logging, alerting, and replan context injection."
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
        "  Igor: instantiate_template('tpl-layer3-scope-check', "
        '\'{"prefix": "MAIN", "code_ref": "ops:check_scope"}\')'
    )


if __name__ == "__main__":
    seed(DB_URL)
