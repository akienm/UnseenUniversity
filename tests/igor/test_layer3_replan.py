"""
test_layer3_replan.py — Tests for REPLAN Layer 3 TEMPLATE node (D297).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null delta (delta is the required trigger)
  7. EMITIF basket pass-through (sub_goals identity default)
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)
 11. test_sub_goals_overwritten_in_basket — sub_goals is overwritten, not appended
 12. test_stopif_fires_on_absent_delta — delta is the required trigger signal

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

from claudecode.seed_layer3_replan import (
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
    code_ref="ops:replan_goals",
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
    # Template cell has: ["FORKIF", ["replan_confidence", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["replan_confidence", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["replan_cell"])
    # Always resolve the Jinja2 FORKIF target placeholder.
    # next_node=None → "None" (node_executor skips falsy/None targets).
    # next_node="MAIN_DECOMPOSE" → "MAIN_DECOMPOSE" (spawned on condition True).
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "FORKIF":
            instr[2] = str(next_node) if next_node is not None else "None"

    # Build the actual payload (string slots already resolved for test purposes)
    payload = {
        "NARRATIVE": payload_template["NARRATIVE"],
        "code_ref": code_ref,
        "default_confidence": str(default_confidence),
        "replan_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "replan_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["delta", "sub_goals"],
        "basket_writes": ["sub_goals", "replan_confidence"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_REPLAN"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-replan"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "REPLAN"

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
        assert "delta" in contract["reads"]
        assert "sub_goals" in contract["reads"]
        assert "sub_goals" in contract["writes"]
        assert "replan_confidence" in contract["writes"]

    def test_basket_contract_notes_overwrite(self):
        """basket_contract side_effects must note that sub_goals is overwritten."""
        side_effects = TEMPLATE_SCHEMA["basket_contract"].get("side_effects", [])
        overwrite_noted = any(
            "overwrite" in s.lower() or "overwritten" in s.lower() for s in side_effects
        )
        assert (
            overwrite_noted
        ), "basket_contract.side_effects must note sub_goals overwrite"


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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-replan"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "replan_cell"

    def test_expansion_metadata_basket_reads(self):
        reads = self.expansion["metadata"]["basket_reads"]
        assert "delta" in reads
        assert "sub_goals" in reads

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "sub_goals" in writes
        assert "replan_confidence" in writes

    def test_expansion_payload_has_replan_cell(self):
        assert "replan_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["replan_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # at minimum: sub_goals set, confidence in range, guard

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )

    def test_instantiation_contract_chaining_note_mentions_decompose(self):
        """chaining_note must mention DECOMPOSE — REPLAN → DECOMPOSE is the re-entrant loop."""
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"]
        assert "DECOMPOSE" in note, "chaining_note must mention REPLAN → DECOMPOSE loop"


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the replan_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["replan_cell"]

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

    def test_emitif_writes_replan_confidence_to_basket(self):
        """At least one EMITIF must write to basket key 'replan_confidence'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "replan_confidence"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing replan_confidence to basket"

    def test_stopif_guards_on_delta(self):
        """STOPIF condition must check delta (the required trigger signal)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                assert isinstance(condition, list), "STOPIF condition must be a list"
                assert (
                    condition[0] == "delta"
                ), f"STOPIF must guard on delta, got: {condition[0]}"

    def test_forkif_condition_checks_replan_confidence(self):
        """FORKIF condition must check replan_confidence (the output confidence slot)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "replan_confidence"
                ), f"FORKIF must check replan_confidence, got: {condition[0]}"


# ── 5. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_sub_goals_and_confidence(self):
        """With delta present, sub_goals and replan_confidence are written."""
        mem = make_expanded_habit()
        basket = {
            "delta": "step 2 failed: missing input validation",
            "sub_goals": ["validate input", "call API", "format output"],
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("sub_goals") is not None
        assert "replan_confidence" in basket

    def test_replan_confidence_is_float(self):
        """replan_confidence emitted must be numeric (float or int)."""
        mem = make_expanded_habit(default_confidence=0.6)
        basket = {
            "delta": "API call returned 404",
            "sub_goals": ["call API"],
        }
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("replan_confidence")
        assert confidence is not None
        # payload stores as string "0.6"; node_executor passes through as-is
        # (string is acceptable — downstream callers cast to float)
        assert float(confidence) == pytest.approx(0.6)

    def test_default_confidence_is_0_6(self):
        """Default confidence for REPLAN is 0.6."""
        mem = make_expanded_habit()  # uses default 0.6
        basket = {
            "delta": "test failed",
            "sub_goals": ["write test", "run test"],
        }
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("replan_confidence")
        assert float(confidence) == pytest.approx(0.6)

    def test_stopif_fires_on_missing_delta(self):
        """Absent delta triggers STOPIF — no writes to sub_goals or replan_confidence."""
        mem = make_expanded_habit()
        basket = {"sub_goals": ["step 1", "step 2"]}  # delta absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("replan_confidence") is None

    def test_stopif_fires_on_null_delta(self):
        """Explicit None delta also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"delta": None, "sub_goals": ["step 1"]}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {
            "delta": "step failed",
            "sub_goals": ["step 1"],
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and confidence is set."""
        mem = make_expanded_habit(next_node="MAIN_DECOMPOSE")
        basket = {
            "delta": "step 3 blocked by missing dependency",
            "sub_goals": ["step 1", "step 2", "step 3"],
        }
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_DECOMPOSE" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_DECOMPOSE")
        basket = {
            "delta": "plan needs revision",
            "sub_goals": ["step 1"],
        }
        result = execute_node(mem, "__entry__", basket)

        assert "TEST_DECOMPOSE" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution (delta present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {
            "delta": "delta present",
            "sub_goals": ["step 1"],
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {
            "delta": "something",
            "sub_goals": ["step 1"],
        }
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("replan_confidence") is None

    def test_basket_pass_through_default_sub_goals(self):
        """Default scaffold: sub_goals pass-through (identity — list unchanged when code_ref not wired)."""
        mem = make_expanded_habit()
        original_goals = ["step 1", "step 2", "step 3"]
        basket = {
            "delta": "step 2 failed",
            "sub_goals": original_goals,
        }
        execute_node(mem, "__entry__", basket)

        # Default pass-through reads from basket.sub_goals and writes back.
        # The list reference may change but the content is preserved.
        assert basket.get("sub_goals") == original_goals

    def test_sub_goals_overwritten_in_basket(self):
        """
        Verify basket.sub_goals is updated (overwritten), not appended to.

        REPLAN's design contract: it OVERWRITES sub_goals, not appends.
        The scaffold default is a pass-through (sub_goals → sub_goals).
        This test verifies that after execution, basket.sub_goals is the
        value that was emitted by EMITIF — not a concatenation of old + new.

        In production, code_ref replaces the pass-through with a real revised list.
        Here we verify the overwrite semantic by checking that the basket key
        holds exactly what EMITIF wrote (the pass-through value), not a union.
        """
        mem = make_expanded_habit()
        initial_goals = ["write tests", "run linter", "deploy"]
        basket = {
            "delta": "deploy step failed",
            "sub_goals": initial_goals,
        }
        execute_node(mem, "__entry__", basket)

        # After execution, sub_goals must be exactly the emitted value.
        # The scaffold emits ["basket", "sub_goals"] → same list.
        # Crucially: it must NOT be initial_goals + emitted_goals (no append).
        result_goals = basket.get("sub_goals")
        assert result_goals is not None

        # Verify no duplication / appending occurred
        # If it were appended, result would be 2× as long as the original
        assert len(result_goals) == len(initial_goals), (
            f"sub_goals length changed from {len(initial_goals)} to {len(result_goals)}. "
            "REPLAN must overwrite sub_goals, not append."
        )

    def test_stopif_fires_on_absent_delta(self):
        """
        delta is the required trigger — absent delta means no replanning needed.

        STOPIF on missing delta is a key design invariant: REPLAN only runs when
        OBSERVE has detected a gap (delta). Without delta, REPLAN is a no-op.
        This prevents spurious replanning when the pipeline hasn't observed anything.
        """
        mem = make_expanded_habit()

        # Case 1: delta key entirely absent
        basket_no_delta = {"sub_goals": ["step 1", "step 2"]}
        result = execute_node(mem, "__entry__", basket_no_delta)
        assert result.stopped_by == "STOPIF", (
            "REPLAN must STOPIF when delta is absent — "
            "delta is the trigger signal for replanning"
        )
        assert basket_no_delta.get("replan_confidence") is None
        assert basket_no_delta.get("sub_goals") == [
            "step 1",
            "step 2",
        ], "sub_goals must not be modified when STOPIF fires on absent delta"

        # Case 2: delta explicitly None
        basket_null_delta = {"delta": None, "sub_goals": ["step 1"]}
        result2 = execute_node(mem, "__entry__", basket_null_delta)
        assert result2.stopped_by == "STOPIF", "REPLAN must STOPIF when delta is None"
        assert basket_null_delta.get("replan_confidence") is None

    def test_chained_after_observe(self):
        """REPLAN correctly reads basket.delta set by a prior OBSERVE node."""
        mem = make_expanded_habit(next_node="MAIN_DECOMPOSE")
        # Simulate OBSERVE having already written delta to basket
        basket = {
            "sub_goals": ["parse input", "call API", "format result"],
            "delta": "API returned 503 — retry needed; add exponential backoff sub-goal",
            "observe_confidence": "0.9",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "replan_confidence" in basket
        assert basket.get("sub_goals") is not None
        assert "MAIN_DECOMPOSE" in result.spawned


# ── 6. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "REPLAN"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["replan_cell"], list)
        assert loaded["replan_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert "delta" in loaded["basket_contract"]["reads"]
        assert "sub_goals" in loaded["basket_contract"]["reads"]
        assert "sub_goals" in loaded["basket_contract"]["writes"]
        assert "replan_confidence" in loaded["basket_contract"]["writes"]


# ── 7. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in replan_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["replan_cell"]

    def test_cell_contains_cognitive_milieu_emitif(self):
        """At least one EMITIF must target cognitive_milieu channel (D300 TWM write)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF targeting cognitive_milieu in replan_cell"

    def test_cognitive_milieu_emitif_key_is_plan_ready(self):
        """The cognitive_milieu EMITIF must write key 'PLAN_READY' (D300).
        REPLAN uses the same key as DECOMPOSE — both update the current plan in TWM.
        """
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
