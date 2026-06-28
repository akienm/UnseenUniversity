"""
test_layer3_decompose.py — Tests for DECOMPOSE Layer 3 TEMPLATE node (D297/D298).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null parsed_goal
  7. EMITIF scaffold defaults (sub_goals=[], dependency_map={}, decompose_confidence)
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)
 11. Re-entrance: second execution overwrites sub_goals/dependency_map

No live DB required — mock cortex / execute_node called directly against the
expanded habit payload using the same MockMemory pattern as test_node_executor.py.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unseen_university.devices.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_decompose import (
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
    code_ref="ops:decompose_goal",
    next_node=None,
    default_confidence=0.6,
):
    """
    Simulate template expansion: return a MockMemory that looks like the
    PROCEDURAL habit the expansion_schema would produce.

    The opcode cell uses "{{ next_node }}" as a Jinja2 placeholder for the
    FORKIF target. At expansion time the engine substitutes the literal node ID.
    This helper simulates that expansion: it replaces the placeholder with the
    actual next_node value (or "None" when next_node is None, in which case
    node_executor's FORKIF skips the falsy target).
    """
    schema = TEMPLATE_SCHEMA
    expansion = schema["expansion_schema"][0]
    payload_template = expansion["payload"]

    # Deep-copy the cell and substitute the Jinja2 FORKIF target placeholder.
    # Template cell has: ["FORKIF", ["decompose_confidence", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["decompose_confidence", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["decompose_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_CONSTRAIN" → "MAIN_CONSTRAIN" (spawned on condition True).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    # Build the actual payload (string slots already resolved for test purposes)
    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "default_confidence": str(default_confidence),
        "decompose_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "decompose_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["parsed_goal"],
        "basket_writes": ["sub_goals", "dependency_map", "decompose_confidence"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_DECOMPOSE"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-decompose"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "DECOMPOSE"

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
        assert "parsed_goal" in contract["reads"]
        assert "sub_goals" in contract["writes"]
        assert "dependency_map" in contract["writes"]
        assert "decompose_confidence" in contract["writes"]


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

    def test_default_confidence_default_is_0_6(self):
        assert self.slots["default_confidence"]["default"] == 0.6

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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-decompose"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "decompose_cell"

    def test_expansion_metadata_basket_reads(self):
        assert "parsed_goal" in self.expansion["metadata"]["basket_reads"]

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "sub_goals" in writes
        assert "dependency_map" in writes
        assert "decompose_confidence" in writes

    def test_expansion_payload_has_decompose_cell(self):
        assert "decompose_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["decompose_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # guard + output writes + confidence range

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )

    def test_chaining_note_mentions_reentrant(self):
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"].lower()
        assert "replan" in note or "re-entr" in note


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the decompose_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["decompose_cell"]

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

    def test_emitif_writes_sub_goals_to_basket(self):
        """At least one EMITIF must write to basket key 'sub_goals'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "sub_goals"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing sub_goals to basket"

    def test_emitif_writes_dependency_map_to_basket(self):
        """At least one EMITIF must write to basket key 'dependency_map'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "dependency_map"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing dependency_map to basket"

    def test_emitif_writes_decompose_confidence_to_basket(self):
        """At least one EMITIF must write to basket key 'decompose_confidence'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "decompose_confidence"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing decompose_confidence to basket"

    def test_stopif_guards_on_parsed_goal(self):
        """STOPIF condition must check parsed_goal (not some other key)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                assert isinstance(condition, list), "STOPIF condition must be a list"
                assert (
                    condition[0] == "parsed_goal"
                ), f"STOPIF must guard on parsed_goal, got: {condition[0]}"

    def test_forkif_condition_checks_decompose_confidence(self):
        """FORKIF condition must check decompose_confidence."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "decompose_confidence"
                ), f"FORKIF must check decompose_confidence, got: {condition[0]}"

    def test_sub_goals_scaffold_default_is_empty_list(self):
        """EMITIF for sub_goals must emit [] as the scaffold default."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "sub_goals"
            ):
                assert (
                    instr[3] == []
                ), f"sub_goals scaffold default must be [], got: {instr[3]!r}"

    def test_dependency_map_scaffold_default_is_empty_dict(self):
        """EMITIF for dependency_map must emit {} as the scaffold default."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "dependency_map"
            ):
                assert (
                    instr[3] == {}
                ), f"dependency_map scaffold default must be {{}}, got: {instr[3]!r}"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_sub_goals(self):
        """With parsed_goal present, sub_goals and dependency_map are written."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "how do I write a factorial function?"}
        result = execute_node(mem, "__entry__", basket)

        assert "sub_goals" in basket
        assert "dependency_map" in basket

    def test_sub_goals_scaffold_default_is_empty_list(self):
        """Default scaffold emits sub_goals = [] (empty list)."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "build a web app"}
        execute_node(mem, "__entry__", basket)

        assert basket.get("sub_goals") == []

    def test_dependency_map_scaffold_default_is_empty_dict(self):
        """Default scaffold emits dependency_map = {} (empty dict)."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "build a web app"}
        execute_node(mem, "__entry__", basket)

        assert basket.get("dependency_map") == {}

    def test_decompose_confidence_is_float(self):
        """decompose_confidence emitted must be numeric (float or int)."""
        mem = make_expanded_habit(default_confidence=0.6)
        basket = {"parsed_goal": "remind me to call John"}
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("decompose_confidence")
        assert confidence is not None
        # payload stores as string "0.6"; node_executor passes through as-is
        # (string is acceptable — downstream callers cast to float)
        assert float(confidence) == pytest.approx(0.6)

    def test_default_confidence_is_0_6(self):
        """Default confidence for DECOMPOSE is 0.6."""
        mem = make_expanded_habit()  # uses default 0.6
        basket = {"parsed_goal": "decompose this task"}
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("decompose_confidence")
        assert float(confidence) == pytest.approx(0.6)

    def test_stopif_fires_on_missing_parsed_goal(self):
        """Absent parsed_goal triggers STOPIF — no writes to sub_goals."""
        mem = make_expanded_habit()
        basket = {}  # parsed_goal absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("sub_goals") is None
        assert basket.get("dependency_map") is None
        assert basket.get("decompose_confidence") is None

    def test_stopif_fires_on_null_parsed_goal(self):
        """Explicit None parsed_goal also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": None}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("sub_goals") is None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {"parsed_goal": "what time is it?"}
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and decompose_confidence is set."""
        mem = make_expanded_habit(next_node="MAIN_CONSTRAIN")
        basket = {"parsed_goal": "summarise my emails"}
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_CONSTRAIN" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_CONSTRAIN")
        basket = {"parsed_goal": "plan my day"}
        result = execute_node(mem, "__entry__", basket)

        assert "TEST_CONSTRAIN" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution (parsed_goal present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "book a meeting with Leah"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "anything"}
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("sub_goals") is None

    def test_chained_after_situate(self):
        """DECOMPOSE correctly reads basket.parsed_goal set by a prior SITUATE node."""
        mem = make_expanded_habit(next_node="MAIN_CONSTRAIN")
        # Simulate PARSE_GOAL + SITUATE having already written to basket
        basket = {
            "user_input": "explain binary search",
            "parsed_goal": "explain binary search algorithm",
            "parse_confidence": "0.5",
            "twm_loaded": True,
            "situate_confidence": "0.7",
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("sub_goals") == []
        assert basket.get("dependency_map") == {}
        assert "decompose_confidence" in basket
        assert "MAIN_CONSTRAIN" in result.spawned

    def test_reentrant_overwrites_previous_sub_goals(self):
        """Re-entering DECOMPOSE after REPLAN overwrites prior sub_goals."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "build API"}
        # First pass — scaffold sets sub_goals = []
        execute_node(mem, "__entry__", basket)
        assert basket.get("sub_goals") == []

        # Simulate REPLAN injecting a real sub_goals list
        basket["sub_goals"] = ["design schema", "write endpoints", "add tests"]
        # Second pass — scaffold overwrites with [] again (real code_ref would populate)
        basket["parsed_goal"] = "build REST API with auth"
        execute_node(mem, "__entry__", basket)
        # Scaffold default overwrites with [] — real code_ref would produce real list
        assert basket.get("sub_goals") == []

    def test_all_three_outputs_written_together(self):
        """sub_goals, dependency_map, and decompose_confidence are all written in one pass."""
        mem = make_expanded_habit(default_confidence=0.75)
        basket = {"parsed_goal": "write a sorting algorithm"}
        execute_node(mem, "__entry__", basket)

        assert "sub_goals" in basket
        assert "dependency_map" in basket
        assert "decompose_confidence" in basket
        assert float(basket["decompose_confidence"]) == pytest.approx(0.75)


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "DECOMPOSE"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["decompose_cell"], list)
        assert loaded["decompose_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert loaded["basket_contract"]["reads"] == ["parsed_goal"]
        assert "sub_goals" in loaded["basket_contract"]["writes"]
        assert "dependency_map" in loaded["basket_contract"]["writes"]
        assert "decompose_confidence" in loaded["basket_contract"]["writes"]

    def test_scaffold_defaults_survive_json_roundtrip(self):
        """[] and {} scaffold defaults must survive json serialisation."""
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        cell = loaded["decompose_cell"]
        sub_goals_emitif = next(
            (
                i
                for i in cell
                if isinstance(i, list) and len(i) == 5 and i[2] == "sub_goals"
            ),
            None,
        )
        dep_map_emitif = next(
            (
                i
                for i in cell
                if isinstance(i, list) and len(i) == 5 and i[2] == "dependency_map"
            ),
            None,
        )
        assert sub_goals_emitif is not None
        assert sub_goals_emitif[3] == []
        assert dep_map_emitif is not None
        assert dep_map_emitif[3] == {}


# ── 8. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in decompose_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["decompose_cell"]

    def test_cell_contains_cognitive_milieu_emitif(self):
        """At least one EMITIF must target cognitive_milieu channel (D300 TWM write)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF targeting cognitive_milieu in decompose_cell"

    def test_cognitive_milieu_emitif_key_is_plan_ready(self):
        """The cognitive_milieu EMITIF must write key 'PLAN_READY' (D300)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[2] == "PLAN_READY"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF writing PLAN_READY to cognitive_milieu"

    def test_cognitive_milieu_emitif_precedes_forkif(self):
        """The cognitive_milieu EMITIF must appear before FORKIF in the cell."""
        emitif_idx = None
        forkif_idx = None
        for i, instr in enumerate(self.cell):
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[4] == "cognitive_milieu"
                and emitif_idx is None
            ):
                emitif_idx = i
            if isinstance(instr, list) and instr[0] == "FORKIF" and forkif_idx is None:
                forkif_idx = i
        assert emitif_idx is not None, "No cognitive_milieu EMITIF found"
        assert forkif_idx is not None, "No FORKIF found"
        assert emitif_idx < forkif_idx, "cognitive_milieu EMITIF must precede FORKIF"
