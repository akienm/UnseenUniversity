"""
test_layer3_constrain.py — Tests for CONSTRAIN Layer 3 TEMPLATE node (D297).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null sub_goals
  7. EMITIF optimistic defaults (constraint_ok=True, violations=[])
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. FORKIF fires regardless of constraint_ok value (True or False)
 10. ENDIF terminator present
 11. Template node metadata shape (as deposited by seed script)

No live DB required — mock cortex / execute_node called directly against the
expanded habit payload using the same MockMemory pattern as test_node_executor.py.
"""

import copy
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devices.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_constrain import (
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
    code_ref="ops:check_constraints",
    next_node=None,
    strict_mode=False,
    # Override the cell emissions for testing non-default constraint results
    override_constraint_ok=None,
    override_violations=None,
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
    # Template cell has: ["FORKIF", ["constraint_ok", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["constraint_ok", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["constrain_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_EXECUTE" → "MAIN_EXECUTE" (spawned on condition True).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    # Allow tests to override the EMITIF values for constraint_ok / violations
    # to simulate a real code_ref returning non-default results.
    if override_constraint_ok is not None or override_violations is not None:
        for instr in cell:
            if isinstance(instr, list) and len(instr) == 5 and instr[0] == "EMITIF":
                if instr[2] == "constraint_ok" and override_constraint_ok is not None:
                    instr[3] = override_constraint_ok
                elif instr[2] == "violations" and override_violations is not None:
                    instr[3] = override_violations

    # Build the actual payload (string slots already resolved for test purposes)
    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "constrain_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "constrain_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["sub_goals", "risk_signals"],
        "basket_writes": ["constraint_ok", "violations"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_CONSTRAIN"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-constrain"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "CONSTRAIN"

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
        assert "sub_goals" in contract["reads"]
        assert "risk_signals" in contract["reads"]
        assert "constraint_ok" in contract["writes"]
        assert "violations" in contract["writes"]


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

    def test_strict_mode_slot_is_optional(self):
        assert self.slots["strict_mode"]["required"] is False

    def test_strict_mode_default_is_false(self):
        assert self.slots["strict_mode"]["default"] is False

    def test_strict_mode_type_is_bool(self):
        assert self.slots["strict_mode"]["type_hint"] == "bool"

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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-constrain"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "constrain_cell"

    def test_expansion_metadata_basket_reads(self):
        reads = self.expansion["metadata"]["basket_reads"]
        assert "sub_goals" in reads
        assert "risk_signals" in reads

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "constraint_ok" in writes
        assert "violations" in writes

    def test_expansion_payload_has_constrain_cell(self):
        assert "constrain_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["constrain_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # at minimum: constraint_ok set, violations list, guard

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )

    def test_chaining_note_mentions_decompose_and_hypothesize(self):
        """CONSTRAIN is used after both DECOMPOSE and HYPOTHESIZE — noted in contract."""
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"]
        assert "DECOMPOSE" in note
        assert "HYPOTHESIZE" in note


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the constrain_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["constrain_cell"]

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

    def test_emitif_writes_constraint_ok_to_basket(self):
        """At least one EMITIF must write to basket key 'constraint_ok'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "constraint_ok"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing constraint_ok to basket"

    def test_emitif_writes_violations_to_basket(self):
        """At least one EMITIF must write to basket key 'violations'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "violations"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing violations to basket"

    def test_stopif_guards_on_sub_goals(self):
        """STOPIF condition must check sub_goals (not some other key)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                assert isinstance(condition, list), "STOPIF condition must be a list"
                assert (
                    condition[0] == "sub_goals"
                ), f"STOPIF must guard on sub_goals, got: {condition[0]}"

    def test_forkif_condition_checks_constraint_ok(self):
        """FORKIF condition must check constraint_ok != None."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "constraint_ok"
                ), f"FORKIF must check constraint_ok, got: {condition[0]}"
                assert (
                    condition[1] == "!="
                ), f"FORKIF operator must be !=, got: {condition[1]}"
                assert (
                    condition[2] is None
                ), f"FORKIF rhs must be None, got: {condition[2]}"

    def test_emitif_constraint_ok_default_is_true(self):
        """Default scaffold emits constraint_ok = True (optimistic default)."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "constraint_ok"
            ):
                assert (
                    instr[3] is True
                ), f"constraint_ok default must be True, got: {instr[3]}"

    def test_emitif_violations_default_is_empty_list(self):
        """Default scaffold emits violations = [] (empty list)."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "violations"
            ):
                assert instr[3] == [], f"violations default must be [], got: {instr[3]}"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_constraint_ok(self):
        """With sub_goals present, constraint_ok and violations are written."""
        mem = make_expanded_habit()
        basket = {
            "sub_goals": ["write factorial function", "add tests"],
            "risk_signals": {},
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("constraint_ok") is not None
        assert "violations" in basket

    def test_constraint_ok_default_is_true(self):
        """Default scaffold emits constraint_ok = True (optimistic default)."""
        mem = make_expanded_habit()
        basket = {
            "sub_goals": ["refactor brainstem"],
            "risk_signals": {"inertia": "HIGH"},
        }
        execute_node(mem, "__entry__", basket)

        assert basket.get("constraint_ok") is True

    def test_violations_default_is_empty_list(self):
        """Default scaffold emits violations = [] (no violations found)."""
        mem = make_expanded_habit()
        basket = {
            "sub_goals": ["write new tool"],
            "risk_signals": {},
        }
        execute_node(mem, "__entry__", basket)

        assert basket.get("violations") == []

    def test_stopif_fires_on_missing_sub_goals(self):
        """Absent sub_goals triggers STOPIF — no writes to constraint_ok."""
        mem = make_expanded_habit()
        basket = {}  # sub_goals absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("constraint_ok") is None
        assert basket.get("violations") is None

    def test_stopif_fires_on_null_sub_goals(self):
        """Explicit None sub_goals also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"sub_goals": None}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("constraint_ok") is None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {
            "sub_goals": ["add logging"],
            "risk_signals": {},
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and constraint_ok is set."""
        mem = make_expanded_habit(next_node="MAIN_EXECUTE")
        basket = {
            "sub_goals": ["write unit tests"],
            "risk_signals": {},
        }
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_EXECUTE" in result.spawned

    def test_forkif_spawns_even_when_constraint_ok_is_false(self):
        """FORKIF fires regardless of constraint_ok value — caller branches on the bool."""
        mem = make_expanded_habit(
            next_node="MAIN_HYPOTHESIZE",
            override_constraint_ok=False,
            override_violations=["inertia_HIGH_brainstem", "scope_boundary_exceeded"],
        )
        basket = {
            "sub_goals": ["rewrite brainstem"],
            "risk_signals": {"inertia": "HIGH"},
        }
        result = execute_node(mem, "__entry__", basket)

        # constraint_ok is False, but FORKIF still fires (condition: != None)
        assert basket.get("constraint_ok") is False
        assert "MAIN_HYPOTHESIZE" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_EXECUTE")
        basket = {
            "sub_goals": ["plan my day"],
            "risk_signals": {},
        }
        result = execute_node(mem, "__entry__", basket)

        # Verify it's the actual node ID, not "{{ next_node }}"
        assert "TEST_EXECUTE" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_violations_propagated_when_overridden(self):
        """When code_ref detects violations, violations list is non-empty in basket."""
        mem = make_expanded_habit(
            override_constraint_ok=False,
            override_violations=["HIGH_inertia_brainstem"],
        )
        basket = {
            "sub_goals": ["delete brainstem module"],
            "risk_signals": {"inertia": "HIGH"},
        }
        execute_node(mem, "__entry__", basket)

        assert basket.get("constraint_ok") is False
        assert "HIGH_inertia_brainstem" in basket.get("violations", [])

    def test_execution_stops_at_endif(self):
        """Normal execution (sub_goals present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {
            "sub_goals": ["add dashboard widget"],
            "risk_signals": {},
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {
            "sub_goals": ["anything"],
            "risk_signals": {},
        }
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("constraint_ok") is None

    def test_risk_signals_not_required_for_guard(self):
        """STOPIF only guards on sub_goals — risk_signals absent is acceptable."""
        mem = make_expanded_habit()
        basket = {"sub_goals": ["write tests"]}  # risk_signals absent
        result = execute_node(mem, "__entry__", basket)

        # Should NOT trigger STOPIF — sub_goals is present
        assert result.stopped_by != "STOPIF"
        assert basket.get("constraint_ok") is True

    def test_chained_after_decompose(self):
        """CONSTRAIN correctly reads basket.sub_goals set by a prior DECOMPOSE node."""
        mem = make_expanded_habit(next_node="MAIN_EXECUTE")
        # Simulate DECOMPOSE having already written sub_goals to basket
        basket = {
            "user_input": "refactor the logging module",
            "parsed_goal": "refactor logging module for clarity",
            "twm_loaded": True,
            "sub_goals": [
                "audit current logging",
                "redesign interface",
                "update callers",
            ],
            "risk_signals": {"scope": "medium"},
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("constraint_ok") is True
        assert "violations" in basket
        assert "MAIN_EXECUTE" in result.spawned


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "CONSTRAIN"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["constrain_cell"], list)
        assert loaded["constrain_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert "sub_goals" in loaded["basket_contract"]["reads"]
        assert "constraint_ok" in loaded["basket_contract"]["writes"]
        assert "violations" in loaded["basket_contract"]["writes"]

    def test_violations_default_survives_json_roundtrip(self):
        """Empty list violations default must survive JSON serialisation."""
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        # Find the violations EMITIF
        for instr in loaded["constrain_cell"]:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "violations"
            ):
                assert (
                    instr[3] == []
                ), f"violations default must be [] after roundtrip, got: {instr[3]}"


# ── 8. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in constrain_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["constrain_cell"]

    def test_cell_contains_cognitive_milieu_emitif(self):
        """At least one EMITIF must target cognitive_milieu channel (D300 TWM write)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF targeting cognitive_milieu in constrain_cell"

    def test_cognitive_milieu_emitif_key_is_constraint_violation(self):
        """The cognitive_milieu EMITIF must write key 'CONSTRAINT_VIOLATION' (D300)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[2] == "CONSTRAINT_VIOLATION"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF writing CONSTRAINT_VIOLATION to cognitive_milieu"

    def test_cognitive_milieu_emitif_is_conditional_on_failure(self):
        """CONSTRAINT_VIOLATION EMITIF must be conditional — only fires when constraint_ok is False."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "CONSTRAINT_VIOLATION"
                and instr[4] == "cognitive_milieu"
            ):
                condition = instr[1]
                assert (
                    condition is not True
                ), "CONSTRAINT_VIOLATION EMITIF must be conditional (not unconditional True)"
                return
        assert False, "CONSTRAINT_VIOLATION EMITIF not found"

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
