"""
test_layer3_observe.py — Tests for OBSERVE Layer 3 TEMPLATE node (D297/D298).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null actual
  7. EMITIF basket pass-through (delta = actual default)
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)

No live DB required — mock cortex / execute_node called directly against the
expanded habit payload using the same MockMemory pattern as test_node_executor.py.
"""

import json
import sys
import os
import copy

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unseen_university.devices.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_observe import (
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
    code_ref="ops:compute_delta",
    next_node=None,
    default_confidence=0.8,
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
    # Template cell has: ["FORKIF", ["delta", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["delta", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["observe_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_REPLAN" → "MAIN_REPLAN" (spawned on condition True).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    # Build the actual payload (string slots already resolved for test purposes)
    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "default_confidence": str(default_confidence),
        "observe_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "observe_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["actual", "expected"],
        "basket_writes": ["delta", "observation_confidence"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_OBSERVE"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-observe"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "OBSERVE"

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
        assert "actual" in contract["reads"]
        assert "delta" in contract["writes"]
        assert "observation_confidence" in contract["writes"]

    def test_basket_contract_reads_expected(self):
        contract = TEMPLATE_SCHEMA["basket_contract"]
        # expected is optional input but declared in reads
        assert "expected" in contract["reads"]


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

    def test_default_confidence_default_is_0_8(self):
        assert self.slots["default_confidence"]["default"] == 0.8

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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-observe"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "observe_cell"

    def test_expansion_metadata_basket_reads(self):
        reads = self.expansion["metadata"]["basket_reads"]
        assert "actual" in reads
        assert "expected" in reads

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "delta" in writes
        assert "observation_confidence" in writes

    def test_expansion_payload_has_observe_cell(self):
        assert "observe_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["observe_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # at minimum: delta set, confidence in range, guard

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )

    def test_chaining_note_mentions_replan_and_hypothesize(self):
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"]
        assert "REPLAN" in note
        assert "HYPOTHESIZE" in note


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the observe_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["observe_cell"]

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

    def test_emitif_writes_delta_to_basket(self):
        """At least one EMITIF must write to basket key 'delta'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "delta"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing delta to basket"

    def test_emitif_writes_observation_confidence_to_basket(self):
        """At least one EMITIF must write to basket key 'observation_confidence'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "observation_confidence"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing observation_confidence to basket"

    def test_stopif_guards_on_actual(self):
        """STOPIF condition must check actual (not some other key)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                assert isinstance(condition, list), "STOPIF condition must be a list"
                assert (
                    condition[0] == "actual"
                ), f"STOPIF must guard on actual, got: {condition[0]}"

    def test_forkif_condition_checks_delta(self):
        """FORKIF condition must check delta (the produced prediction-error signal)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "delta"
                ), f"FORKIF must check delta, got: {condition[0]}"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_delta(self):
        """With actual present, delta and observation_confidence are written."""
        mem = make_expanded_habit()
        basket = {"actual": "the function returned 42"}
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("delta") is not None
        assert "observation_confidence" in basket

    def test_delta_is_actual_pass_through_default(self):
        """Default scaffold: delta == actual (pass-through when no expected baseline)."""
        mem = make_expanded_habit()
        actual_value = "model predicted rain but it was sunny"
        basket = {"actual": actual_value}
        execute_node(mem, "__entry__", basket)

        assert basket.get("delta") == actual_value

    def test_observation_confidence_is_float(self):
        """observation_confidence emitted must be numeric (float or int)."""
        mem = make_expanded_habit(default_confidence=0.8)
        basket = {"actual": "result was 7, expected 5"}
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("observation_confidence")
        assert confidence is not None
        # payload stores as string "0.8"; node_executor passes through as-is
        # (string is acceptable — downstream callers cast to float)
        assert float(confidence) == pytest.approx(0.8)

    def test_default_confidence_is_0_8(self):
        """Default confidence for OBSERVE is 0.8 (observation is high-confidence)."""
        mem = make_expanded_habit()  # uses default 0.8
        basket = {"actual": "observation signal"}
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("observation_confidence")
        assert float(confidence) == pytest.approx(0.8)

    def test_stopif_fires_on_missing_actual(self):
        """Absent actual triggers STOPIF — no writes to delta."""
        mem = make_expanded_habit()
        basket = {}  # actual absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("delta") is None
        assert basket.get("observation_confidence") is None

    def test_stopif_fires_on_null_actual(self):
        """Explicit None actual also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"actual": None}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("delta") is None

    def test_stopif_does_not_fire_when_only_expected_absent(self):
        """STOPIF only guards on actual — expected may be absent (pure observation)."""
        mem = make_expanded_habit()
        basket = {"actual": "something happened"}
        # No "expected" key — this is valid (pure observation mode)
        result = execute_node(mem, "__entry__", basket)

        # Should NOT stop — actual is present
        assert result.stopped_by != "STOPIF"
        assert basket.get("delta") is not None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {"actual": "result was different from expectation"}
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and delta was produced."""
        mem = make_expanded_habit(next_node="MAIN_REPLAN")
        basket = {"actual": "goal not achieved"}
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_REPLAN" in result.spawned

    def test_forkif_spawns_to_hypothesize_target(self):
        """FORKIF can also spawn to a HYPOTHESIZE node — caller decides."""
        mem = make_expanded_habit(next_node="MAIN_HYPOTHESIZE")
        basket = {"actual": "unexpected output from tool"}
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_HYPOTHESIZE" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_REPLAN")
        basket = {"actual": "prediction failed"}
        result = execute_node(mem, "__entry__", basket)

        # Verify it's the actual node ID, not "{{ next_node }}"
        assert "TEST_REPLAN" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution (actual present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {"actual": "output received"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {"actual": "anything"}
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("delta") is None

    def test_with_both_expected_and_actual_in_basket(self):
        """Scaffold works when both expected and actual are present in basket."""
        mem = make_expanded_habit()
        basket = {
            "expected": "the function should return 5",
            "actual": "the function returned 7",
        }
        execute_node(mem, "__entry__", basket)

        # Scaffold pass-through: delta = actual (code_ref would compute real diff)
        assert basket.get("delta") == "the function returned 7"
        assert "observation_confidence" in basket

    def test_chained_after_upstream_brick(self):
        """OBSERVE correctly reads basket.actual set by a prior brick."""
        mem = make_expanded_habit(next_node="MAIN_REPLAN")
        # Simulate a prior brick having set up the prediction context
        basket = {
            "parsed_goal": "complete the task",
            "expected": "task completed successfully",
            "actual": "task failed with error code 42",
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("delta") is not None
        assert "observation_confidence" in basket
        assert "MAIN_REPLAN" in result.spawned


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "OBSERVE"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["observe_cell"], list)
        assert loaded["observe_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert "actual" in loaded["basket_contract"]["reads"]
        assert "delta" in loaded["basket_contract"]["writes"]
        assert "observation_confidence" in loaded["basket_contract"]["writes"]

    def test_template_node_tags_include_predictive_coding(self):
        """Template node tags must mention predictive_coding (D279/D298 lineage)."""
        tags = TEMPLATE_NODE["metadata"]["tags"]
        assert "predictive_coding" in tags or "prediction_error" in tags


# ── 7. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in observe_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["observe_cell"]

    def test_cell_contains_cognitive_milieu_emitif(self):
        """At least one EMITIF must target cognitive_milieu channel (D300 TWM write)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF targeting cognitive_milieu in observe_cell"

    def test_cognitive_milieu_emitif_key_is_delta(self):
        """The cognitive_milieu EMITIF must write key 'DELTA' (D300)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[2] == "DELTA"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF writing DELTA to cognitive_milieu"

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
