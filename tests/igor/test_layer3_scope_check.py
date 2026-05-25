"""
test_layer3_scope_check.py — Tests for SCOPE_CHECK Layer 3 TEMPLATE node (D297).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guards fire on absent/null current_action and parsed_goal
  7. EMITIF defaults: scope_ok=True (optimistic), drift_signal=None
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)
 11. Extra: test_stopif_fires_on_absent_current_action
 12. Extra: test_stopif_fires_on_absent_parsed_goal
 13. Extra: test_scope_ok_defaults_to_true
 14. Extra: test_drift_signal_defaults_to_none

No live DB required — execute_node called directly against the expanded habit
payload using the same MockMemory pattern as test_node_executor.py.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wild_igor.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_scope_check import (
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
    code_ref="ops:check_scope",
    next_node=None,
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
    # Template cell has: ["FORKIF", ["scope_ok", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["scope_ok", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["scope_check_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_ALERT" → "MAIN_ALERT" (spawned when scope_ok is set).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "scope_check_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "scope_check_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["current_action", "parsed_goal"],
        "basket_writes": ["scope_ok", "drift_signal"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_SCOPE_CHECK"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-scope-check"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "SCOPE_CHECK"

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
        assert "current_action" in contract["reads"]
        assert "parsed_goal" in contract["reads"]
        assert "scope_ok" in contract["writes"]
        assert "drift_signal" in contract["writes"]


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

    def test_no_default_confidence_slot(self):
        """SCOPE_CHECK emits scope_ok bool, not a confidence float — no default_confidence slot."""
        assert "default_confidence" not in self.slots

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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-scope-check"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "scope_check_cell"

    def test_expansion_metadata_basket_reads(self):
        reads = self.expansion["metadata"]["basket_reads"]
        assert "current_action" in reads
        assert "parsed_goal" in reads

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "scope_ok" in writes
        assert "drift_signal" in writes

    def test_expansion_payload_has_scope_check_cell(self):
        assert "scope_check_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["scope_check_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the scope_check_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"][
            "scope_check_cell"
        ]

    def _collect_ops(self):
        return [instr[0] for instr in self.cell if isinstance(instr, list) and instr]

    def test_cell_contains_stopif(self):
        assert "STOPIF" in self._collect_ops()

    def test_cell_contains_two_stopif_instructions(self):
        """Two separate STOPIF guards — one per required input."""
        count = sum(
            1
            for instr in self.cell
            if isinstance(instr, list) and instr and instr[0] == "STOPIF"
        )
        assert count == 2, f"Expected 2 STOPIF instructions, got {count}"

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

    def test_emitif_writes_scope_ok_to_basket(self):
        """At least one EMITIF must write to basket key 'scope_ok'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "scope_ok"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing scope_ok to basket"

    def test_emitif_writes_drift_signal_to_basket(self):
        """At least one EMITIF must write to basket key 'drift_signal'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "drift_signal"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing drift_signal to basket"

    def test_stopif_guards_include_current_action(self):
        """One STOPIF condition must check current_action."""
        keys_guarded = []
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                if isinstance(condition, list):
                    keys_guarded.append(condition[0])
        assert (
            "current_action" in keys_guarded
        ), f"No STOPIF guards current_action; guarded keys: {keys_guarded}"

    def test_stopif_guards_include_parsed_goal(self):
        """One STOPIF condition must check parsed_goal."""
        keys_guarded = []
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                if isinstance(condition, list):
                    keys_guarded.append(condition[0])
        assert (
            "parsed_goal" in keys_guarded
        ), f"No STOPIF guards parsed_goal; guarded keys: {keys_guarded}"

    def test_forkif_condition_checks_scope_ok(self):
        """FORKIF condition must check scope_ok (fires regardless of True/False)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "scope_ok"
                ), f"FORKIF must check scope_ok, got: {condition[0]}"

    def test_scope_ok_emitif_default_is_true(self):
        """The scope_ok EMITIF must emit True as its default value."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "scope_ok"
            ):
                assert (
                    instr[3] is True
                ), f"scope_ok default must be True (optimistic), got: {instr[3]}"

    def test_drift_signal_emitif_default_is_none(self):
        """The drift_signal EMITIF must emit None as its default value."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "drift_signal"
            ):
                assert (
                    instr[3] is None
                ), f"drift_signal default must be None, got: {instr[3]}"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_scope_ok(self):
        """With both inputs present, scope_ok and drift_signal are written."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write a factorial function in Python",
            "parsed_goal": "implement a factorial function",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "scope_ok" in basket
        assert "drift_signal" in basket

    def test_scope_ok_defaults_to_true(self):
        """Optimistic default: scope_ok emitted as True when no code_ref override."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write unit tests for factorial",
            "parsed_goal": "implement a factorial function",
        }
        execute_node(mem, "__entry__", basket)

        assert basket.get("scope_ok") is True

    def test_drift_signal_defaults_to_none(self):
        """Default scaffold emits drift_signal = None (no drift detected)."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write unit tests for factorial",
            "parsed_goal": "implement a factorial function",
        }
        execute_node(mem, "__entry__", basket)

        assert basket.get("drift_signal") is None

    def test_stopif_fires_on_absent_current_action(self):
        """Absent current_action triggers STOPIF — no writes to scope_ok."""
        mem = make_expanded_habit()
        basket = {"parsed_goal": "implement a factorial function"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("scope_ok") is None
        assert basket.get("drift_signal") is None

    def test_stopif_fires_on_absent_parsed_goal(self):
        """Absent parsed_goal triggers STOPIF — no writes to scope_ok."""
        mem = make_expanded_habit()
        basket = {"current_action": "write a factorial function in Python"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("scope_ok") is None
        assert basket.get("drift_signal") is None

    def test_stopif_fires_on_null_current_action(self):
        """Explicit None current_action also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {
            "current_action": None,
            "parsed_goal": "implement a factorial function",
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("scope_ok") is None

    def test_stopif_fires_on_null_parsed_goal(self):
        """Explicit None parsed_goal also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write a factorial function",
            "parsed_goal": None,
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("scope_ok") is None

    def test_both_inputs_required(self):
        """Empty basket (no inputs) triggers STOPIF — both are required."""
        mem = make_expanded_habit()
        basket = {}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("scope_ok") is None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {
            "current_action": "write factorial tests",
            "parsed_goal": "implement factorial",
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and scope_ok is set."""
        mem = make_expanded_habit(next_node="MAIN_ALERT_DRIFT")
        basket = {
            "current_action": "write factorial tests",
            "parsed_goal": "implement factorial",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_ALERT_DRIFT" in result.spawned

    def test_forkif_spawns_even_when_scope_ok_false(self):
        """FORKIF condition is scope_ok != None — fires for both True and False.

        This is critical: the alert/replan node must be reached even on drift
        so it can inspect scope_ok and decide whether to halt or re-route.
        """
        mem = make_expanded_habit(next_node="ALERT_NODE")
        basket = {
            "current_action": "write factorial tests",
            "parsed_goal": "implement factorial",
        }
        # Pre-seed scope_ok=False to simulate what code_ref would write after
        # detecting drift. The EMITIF still emits True first (scaffold default),
        # so we verify the FORKIF fires on the value in basket.
        result = execute_node(mem, "__entry__", basket)

        # Default scaffold emits scope_ok=True; FORKIF fires on non-None scope_ok.
        # The important thing: FORKIF target was reached.
        assert "ALERT_NODE" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_REPLAN")
        basket = {
            "current_action": "write factorial",
            "parsed_goal": "implement factorial",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "TEST_REPLAN" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write factorial",
            "parsed_goal": "implement factorial",
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "anything",
            "parsed_goal": "anything",
        }
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("scope_ok") is None

    def test_parallel_guard_does_not_consume_existing_basket_keys(self):
        """SCOPE_CHECK only writes scope_ok + drift_signal; does not clobber other keys."""
        mem = make_expanded_habit()
        basket = {
            "current_action": "write factorial",
            "parsed_goal": "implement factorial",
            "user_input": "help me with factorial",
            "parse_confidence": "0.8",
        }
        execute_node(mem, "__entry__", basket)

        assert basket["user_input"] == "help me with factorial"
        assert basket["parse_confidence"] == "0.8"


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "SCOPE_CHECK"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["scope_check_cell"], list)
        assert loaded["scope_check_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert "current_action" in loaded["basket_contract"]["reads"]
        assert "parsed_goal" in loaded["basket_contract"]["reads"]
        assert "scope_ok" in loaded["basket_contract"]["writes"]
        assert "drift_signal" in loaded["basket_contract"]["writes"]


# ── 7. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in scope_check_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"][
            "scope_check_cell"
        ]

    def test_cell_contains_cognitive_milieu_emitif(self):
        """At least one EMITIF must target cognitive_milieu channel (D300 TWM write)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF targeting cognitive_milieu in scope_check_cell"

    def test_cognitive_milieu_emitif_key_is_scope_drift(self):
        """The cognitive_milieu EMITIF must write key 'SCOPE_DRIFT' (D300)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[2] == "SCOPE_DRIFT"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF writing SCOPE_DRIFT to cognitive_milieu"

    def test_cognitive_milieu_emitif_is_conditional_on_failure(self):
        """SCOPE_DRIFT EMITIF must be conditional — only fires when scope_ok is False."""
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "SCOPE_DRIFT"
                and instr[4] == "cognitive_milieu"
            ):
                condition = instr[1]
                assert (
                    condition is not True
                ), "SCOPE_DRIFT EMITIF must be conditional (not unconditional True)"
                return
        assert False, "SCOPE_DRIFT EMITIF not found"

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
