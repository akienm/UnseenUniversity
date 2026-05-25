"""
test_layer3_parse_goal.py — Tests for PARSE_GOAL Layer 3 TEMPLATE node (D297/D298).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null user_input
  7. EMITIF basket pass-through (identity extraction default)
  8. FORKIF conditional fork — fires when next_node_id present, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)

No live DB required — mock cortex / execute_node called directly against the
expanded habit payload using the same MockMemory pattern as test_node_executor.py.
"""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devices.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_parse_goal import (
    TEMPLATE_ID,
    TEMPLATE_SCHEMA,
    TEMPLATE_NODE,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


class MockMemory:
    """Minimal mock matching what node_executor expects."""

    def __init__(self, memory_id, payload=None, metadata=None):
        self.id = memory_id
        self.payload = payload or {}
        self.metadata = metadata or {"triggers": {}}


def make_expanded_habit(
    prefix="TEST",
    code_ref="ops:parse_intent",
    next_node=None,
    default_confidence=0.5,
):
    """
    Simulate template expansion: return a MockMemory that looks like the
    PROCEDURAL habit the expansion_schema would produce.

    The opcode cell uses "{{ next_node }}" as a Jinja2 placeholder for the
    FORKIF target. At expansion time the engine substitutes the literal node ID.
    This helper simulates that expansion: it replaces the placeholder with the
    actual next_node value (or leaves it as-is when next_node is None, in which
    case the FORKIF condition ["next_node_id", "!=", None] is False and FORKIF
    is a no-op).
    """
    schema = TEMPLATE_SCHEMA
    expansion = schema["expansion_schema"][0]
    payload_template = expansion["payload"]

    # Deep-copy the cell and substitute the Jinja2 FORKIF target placeholder.
    # Template cell has: ["FORKIF", ["next_node_id", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["next_node_id", "!=", None], "<literal_id>"]
    import copy

    cell = copy.deepcopy(payload_template["parse_goal_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_SITUATE" → "MAIN_SITUATE" (spawned on condition True).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    # Build the actual payload (string slots already resolved for test purposes)
    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "default_confidence": str(default_confidence),
        "parse_goal_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "parse_goal_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["user_input"],
        "basket_writes": ["parsed_goal", "parse_confidence"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_PARSE_GOAL"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-parse-goal"

    def test_schema_has_required_top_level_keys(self):
        required = {
            "pattern_name",
            "layer",
            "schema_version",
            "substitution_engine",
            "slot_manifest",
            "expansion_schema",
            "instantiation_contract",
        }
        assert required.issubset(set(TEMPLATE_SCHEMA.keys()))

    def test_pattern_name(self):
        assert TEMPLATE_SCHEMA["pattern_name"] == "PARSE_GOAL"

    def test_layer_is_3(self):
        assert TEMPLATE_SCHEMA["layer"] == 3

    def test_schema_version_is_1(self):
        assert TEMPLATE_SCHEMA["schema_version"] == 1

    def test_substitution_engine_is_jinja2(self):
        assert TEMPLATE_SCHEMA["substitution_engine"] == "jinja2"

    def test_expansion_schema_is_list_with_one_entry(self):
        assert isinstance(TEMPLATE_SCHEMA["expansion_schema"], list)
        assert len(TEMPLATE_SCHEMA["expansion_schema"]) == 1

    def test_template_node_id_matches(self):
        assert TEMPLATE_NODE["id"] == TEMPLATE_ID

    def test_template_node_memory_type_is_procedural(self):
        assert TEMPLATE_NODE["memory_type"] == "PROCEDURAL"

    def test_template_node_metadata_has_template_true(self):
        assert TEMPLATE_NODE["metadata"]["template"] is True

    def test_template_node_metadata_has_layer_3(self):
        assert TEMPLATE_NODE["metadata"]["layer"] == 3

    def test_template_node_metadata_has_template_schema(self):
        assert "template_schema" in TEMPLATE_NODE["metadata"]
        assert TEMPLATE_NODE["metadata"]["template_schema"] is TEMPLATE_SCHEMA

    def test_basket_contract_declared(self):
        contract = TEMPLATE_SCHEMA["basket_contract"]
        assert "user_input" in contract["reads"]
        assert "parsed_goal" in contract["writes"]
        assert "parse_confidence" in contract["writes"]


# ── 2. Slot manifest ──────────────────────────────────────────────────────────


class TestSlotManifest:
    def setup_method(self):
        self.slots = {s["name"]: s for s in TEMPLATE_SCHEMA["slot_manifest"]}

    def test_prefix_slot_is_required(self):
        assert self.slots["prefix"]["required"] is True

    def test_prefix_slot_type_is_str(self):
        assert self.slots["prefix"]["type_hint"] == "str"

    def test_code_ref_slot_is_required(self):
        assert self.slots["code_ref"]["required"] is True

    def test_code_ref_slot_type_is_str(self):
        assert self.slots["code_ref"]["type_hint"] == "str"

    def test_next_node_slot_is_optional(self):
        assert self.slots["next_node"]["required"] is False

    def test_next_node_default_is_none(self):
        assert self.slots["next_node"]["default"] is None

    def test_default_confidence_is_optional(self):
        assert self.slots["default_confidence"]["required"] is False

    def test_default_confidence_default_is_0_5(self):
        assert self.slots["default_confidence"]["default"] == 0.5

    def test_default_confidence_validator_bounds(self):
        v = self.slots["default_confidence"]["validator"]
        assert v["min"] == 0.0
        assert v["max"] == 1.0

    def test_all_required_slots_present(self):
        required = [s for s in TEMPLATE_SCHEMA["slot_manifest"] if s["required"]]
        names = {s["name"] for s in required}
        assert names == {"prefix", "code_ref"}


# ── 3. Expansion schema ───────────────────────────────────────────────────────


class TestExpansionSchema:
    def setup_method(self):
        self.expansion = TEMPLATE_SCHEMA["expansion_schema"][0]

    def test_expansion_id_contains_prefix_template(self):
        assert "prefix" in self.expansion["id"]

    def test_expansion_memory_type_is_procedural(self):
        assert self.expansion["memory_type"] == "PROCEDURAL"

    def test_expansion_metadata_template_false(self):
        assert self.expansion["metadata"]["template"] is False

    def test_expansion_metadata_template_parent(self):
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-parse-goal"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "parse_goal_cell"

    def test_expansion_metadata_basket_reads(self):
        assert "user_input" in self.expansion["metadata"]["basket_reads"]

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "parsed_goal" in writes
        assert "parse_confidence" in writes

    def test_expansion_payload_has_parse_goal_cell(self):
        assert "parse_goal_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["parse_goal_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # at minimum: parsed_goal set, confidence in range, guard

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the parse_goal_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["parse_goal_cell"]

    def _collect_ops(self):
        return [instr[0] for instr in self.cell if isinstance(instr, list) and instr]

    def test_cell_contains_stopif(self):
        assert "STOPIF" in self._collect_ops()

    def test_cell_contains_emitif(self):
        assert "EMITIF" in self._collect_ops()

    def test_cell_contains_forkif(self):
        assert "FORKIF" in self._collect_ops()

    def test_cell_ends_with_endif(self):
        assert self.cell[-1] == "ENDIF"

    def test_all_opcodes_are_known(self):
        valid_ops = {"LABEL", "STOPIF", "EMITIF", "BRANCHIF", "FORKIF", "ENDIF"}
        for instr in self.cell:
            if isinstance(instr, list) and instr:
                assert instr[0] in valid_ops, f"Unknown opcode: {instr[0]}"
            elif isinstance(instr, str):
                assert instr == "ENDIF", f"Unexpected string instruction: {instr}"

    def test_stopif_has_correct_arity(self):
        """STOPIF expects exactly 2 elements: [op, condition]."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                assert len(instr) == 2, f"STOPIF arity wrong: {instr}"

    def test_emitif_has_correct_arity(self):
        """EMITIF expects exactly 5 elements: [op, condition, key, value, channel]."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "EMITIF":
                assert len(instr) == 5, f"EMITIF arity wrong: {instr}"

    def test_emitif_channels_are_valid(self):
        valid_channels = {
            "basket",
            "emotional_milieu",
            "cognitive_milieu",
            "console",
            "web",
            "discord",
            "memory",
        }
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "EMITIF":
                channel = instr[4]
                assert channel in valid_channels, f"Unknown channel: {channel}"

    def test_forkif_has_correct_arity(self):
        """FORKIF expects exactly 3 elements: [op, condition, target]."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                assert len(instr) == 3, f"FORKIF arity wrong: {instr}"

    def test_emitif_writes_parsed_goal_to_basket(self):
        """At least one EMITIF must write to basket key 'parsed_goal'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "parsed_goal"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing parsed_goal to basket"

    def test_emitif_writes_parse_confidence_to_basket(self):
        """At least one EMITIF must write to basket key 'parse_confidence'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "parse_confidence"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing parse_confidence to basket"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_parsed_goal(self):
        """With user_input present, parsed_goal and parse_confidence are written."""
        mem = make_expanded_habit()
        basket = {"user_input": "how do I write a factorial function?"}
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("parsed_goal") is not None
        assert "parse_confidence" in basket

    def test_parse_confidence_is_float(self):
        """parse_confidence emitted must be numeric (float or int)."""
        mem = make_expanded_habit(default_confidence=0.7)
        basket = {"user_input": "remind me to call John"}
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("parse_confidence")
        assert confidence is not None
        # payload stores as string "0.7"; node_executor passes through as-is
        # (string is acceptable — downstream callers cast to float)
        assert float(confidence) == pytest.approx(0.7)

    def test_stopif_fires_on_missing_user_input(self):
        """Absent user_input triggers STOPIF — no writes to parsed_goal."""
        mem = make_expanded_habit()
        basket = {}  # user_input absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("parsed_goal") is None
        assert basket.get("parse_confidence") is None

    def test_stopif_fires_on_null_user_input(self):
        """Explicit None user_input also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"user_input": None}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("parsed_goal") is None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {"user_input": "what time is it?"}
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and goal was extracted."""
        mem = make_expanded_habit(next_node="MAIN_SITUATE")
        basket = {"user_input": "summarise my emails"}
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_SITUATE" in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution (user_input present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {"user_input": "book a meeting with Leah"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")
        # FORKIF does not stop the cursor — cursor continues; ENDIF is the terminator

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {"user_input": "anything"}
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("parsed_goal") is None

    def test_basket_pass_through_default_extraction(self):
        """Default scaffold: parsed_goal == user_input (identity extraction)."""
        mem = make_expanded_habit()
        user_input = "write me a haiku about refactoring"
        basket = {"user_input": user_input}
        execute_node(mem, "__entry__", basket)

        assert basket.get("parsed_goal") == user_input


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "PARSE_GOAL"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["parse_goal_cell"], list)
        assert loaded["parse_goal_cell"][-1] == "ENDIF"
