"""
test_layer3_hypothesize.py — Tests for HYPOTHESIZE Layer 3 TEMPLATE node (D297/D298).

Tests:
  1. Template schema structure — required fields, slot manifest, expansion_schema
  2. Slot manifest validation — required/optional slots, type hints, defaults
  3. Basket contract — reads/writes declared correctly
  4. Expansion schema rendering — Jinja2 substitution with sample slot values
  5. Produced habit payload is valid node_executor syntax
  6. STOPIF guard fires on absent/null delta
  7. EMITIF basket pass-through (hypothesis = delta identity default)
  8. FORKIF conditional fork — fires when next_node provided, skips when absent
  9. ENDIF terminator present
 10. Template node metadata shape (as deposited by seed script)
 11. time_direction declared as a basket read (D298 design requirement)
 12. D298-specific: HYPOTHESIZE collapses ANTICIPATE (chaining_note present)

No live DB required — mock cortex / execute_node called directly against the
expanded habit payload using the same MockMemory pattern as test_node_executor.py.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wild_igor.igor.cognition.node_executor import execute_node, ExecutionResult

# ── Pull schema from seed script (single source of truth) ────────────────────

from claudecode.seed_layer3_hypothesize import (
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
    code_ref="ops:hypothesize",
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
    # Template cell has: ["FORKIF", ["hypothesis", "!=", None], "{{ next_node }}"]
    # Post-expansion:    ["FORKIF", ["hypothesis", "!=", None], "<literal_id>"]
    cell = copy.deepcopy(payload_template["hypothesize_cell"])
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
        "hypothesize_cell": cell,
    }

    metadata = {
        "triggers": {"__entry__": "hypothesize_cell"},
        "habit_type": "cognitive",
        "template": False,
        "basket_reads": ["delta", "twm_loaded", "time_direction"],
        "basket_writes": ["hypothesis", "hypothesis_confidence"],
        "code_ref": code_ref,
        "layer": 3,
    }

    habit_id = f"{prefix}_HYPOTHESIZE"
    return MockMemory(habit_id, payload=payload, metadata=metadata)


# ── 1. Template schema structure ──────────────────────────────────────────────


class TestTemplateSchemaStructure:
    def test_template_id_format(self):
        assert TEMPLATE_ID == "tpl-layer3-hypothesize"

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
        assert TEMPLATE_SCHEMA["pattern_name"] == "HYPOTHESIZE"

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
        assert "hypothesis" in contract["writes"]
        assert "hypothesis_confidence" in contract["writes"]

    def test_time_direction_in_basket_reads(self):
        """D298 requirement: time_direction must be declared as a basket read."""
        contract = TEMPLATE_SCHEMA["basket_contract"]
        assert "time_direction" in contract["reads"], (
            "time_direction must be in basket_contract.reads — "
            "the code_ref tool reads it to control forward/backward orientation (D298)"
        )

    def test_twm_loaded_in_basket_reads(self):
        """TWM context is an input to hypothesis generation."""
        contract = TEMPLATE_SCHEMA["basket_contract"]
        assert "twm_loaded" in contract["reads"]


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
        assert self.expansion["metadata"]["template_parent"] == "tpl-layer3-hypothesize"

    def test_expansion_metadata_layer_3(self):
        assert self.expansion["metadata"]["layer"] == 3

    def test_expansion_metadata_has_triggers(self):
        triggers = self.expansion["metadata"]["triggers"]
        assert "__entry__" in triggers
        assert triggers["__entry__"] == "hypothesize_cell"

    def test_expansion_metadata_basket_reads_includes_delta(self):
        assert "delta" in self.expansion["metadata"]["basket_reads"]

    def test_expansion_metadata_basket_reads_includes_twm_loaded(self):
        assert "twm_loaded" in self.expansion["metadata"]["basket_reads"]

    def test_expansion_metadata_basket_reads_includes_time_direction(self):
        assert "time_direction" in self.expansion["metadata"]["basket_reads"]

    def test_expansion_metadata_basket_writes(self):
        writes = self.expansion["metadata"]["basket_writes"]
        assert "hypothesis" in writes
        assert "hypothesis_confidence" in writes

    def test_expansion_payload_has_hypothesize_cell(self):
        assert "hypothesize_cell" in self.expansion["payload"]

    def test_expansion_payload_cell_is_list(self):
        cell = self.expansion["payload"]["hypothesize_cell"]
        assert isinstance(cell, list)
        assert len(cell) > 0

    def test_expansion_payload_has_narrative(self):
        assert "NARRATIVE" in self.expansion["payload"]

    def test_instantiation_contract_invariants(self):
        inv = TEMPLATE_SCHEMA["instantiation_contract"]["invariants"]
        assert len(inv) >= 3  # at minimum: hypothesis set, confidence in range, guard

    def test_instantiation_contract_edge_policy(self):
        assert (
            TEMPLATE_SCHEMA["instantiation_contract"]["edge_policy"] == "link_to_parent"
        )

    def test_chaining_note_mentions_debug_loop(self):
        """D298 design: HYPOTHESIZE is used in debug loop (OBSERVE → HYPOTHESIZE → CONSTRAIN → REPLAN)."""
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"]
        assert "CONSTRAIN" in note, "chaining_note must mention CONSTRAIN (debug loop)"

    def test_chaining_note_mentions_forward_direction(self):
        """D298: forward time_direction = risk scan (formerly ANTICIPATE)."""
        note = TEMPLATE_SCHEMA["instantiation_contract"]["chaining_note"]
        assert (
            "forward" in note.lower()
        ), "chaining_note must mention forward time_direction (risk scan use case, D298)"


# ── 4. Opcode cell validity ───────────────────────────────────────────────────


class TestOpcodeCellValidity:
    """Verify the hypothesize_cell contains valid node_executor opcodes."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"][
            "hypothesize_cell"
        ]

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

    def test_emitif_writes_hypothesis_to_basket(self):
        """At least one EMITIF must write to basket key 'hypothesis'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "hypothesis"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing hypothesis to basket"

    def test_emitif_writes_hypothesis_confidence_to_basket(self):
        """At least one EMITIF must write to basket key 'hypothesis_confidence'."""
        found = False
        for instr in self.cell:
            if (
                isinstance(instr, list)
                and len(instr) == 5
                and instr[0] == "EMITIF"
                and instr[2] == "hypothesis_confidence"
                and instr[4] == "basket"
            ):
                found = True
                break
        assert found, "No EMITIF writing hypothesis_confidence to basket"

    def test_stopif_guards_on_delta(self):
        """STOPIF condition must check delta (the required signal, not some other key)."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "STOPIF":
                condition = instr[1]
                assert isinstance(condition, list), "STOPIF condition must be a list"
                assert (
                    condition[0] == "delta"
                ), f"STOPIF must guard on delta, got: {condition[0]}"

    def test_forkif_condition_checks_hypothesis(self):
        """FORKIF condition must check hypothesis (D298 spec: FORKIF ["hypothesis", "!=", None])."""
        for instr in self.cell:
            if isinstance(instr, list) and instr and instr[0] == "FORKIF":
                condition = instr[1]
                assert isinstance(condition, list), "FORKIF condition must be a list"
                assert (
                    condition[0] == "hypothesis"
                ), f"FORKIF must check hypothesis, got: {condition[0]}"


# ── 5. D298-specific: time_direction basket read ──────────────────────────────


class TestTimeDirectionBasketRead:
    """D298 design requirement: time_direction controls forward/backward orientation."""

    def test_time_direction_forward_in_basket_reads(self):
        """time_direction must be declared in basket_reads (code_ref reads it at runtime)."""
        expansion = TEMPLATE_SCHEMA["expansion_schema"][0]
        basket_reads = expansion["metadata"]["basket_reads"]
        assert "time_direction" in basket_reads, (
            "time_direction must be in metadata.basket_reads — "
            "the code_ref tool reads it to control forward/backward orientation (D298)"
        )

    def test_time_direction_in_contract_reads(self):
        """basket_contract.reads must include time_direction."""
        reads = TEMPLATE_SCHEMA["basket_contract"]["reads"]
        assert "time_direction" in reads

    def test_scaffold_does_not_branch_on_time_direction(self):
        """The scaffold must NOT contain a BRANCHIF on time_direction — that's code_ref's job."""
        cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]["hypothesize_cell"]
        for instr in cell:
            if isinstance(instr, list) and instr and instr[0] == "BRANCHIF":
                condition = instr[1] if len(instr) > 1 else None
                if (
                    isinstance(condition, list)
                    and condition
                    and condition[0] == "time_direction"
                ):
                    pytest.fail(
                        "Scaffold must not BRANCHIF on time_direction — "
                        "that is the code_ref tool's responsibility (D298 design rule 4)"
                    )


# ── 6. node_executor integration — execute the expanded habit ─────────────────


class TestNodeExecutorIntegration:
    """Execute the expanded habit's payload using the real node_executor."""

    def test_normal_execution_emits_hypothesis(self):
        """With delta present, hypothesis and hypothesis_confidence are written."""
        mem = make_expanded_habit()
        basket = {
            "delta": "user expected greeting but got error",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("hypothesis") is not None
        assert "hypothesis_confidence" in basket

    def test_hypothesis_confidence_is_float(self):
        """hypothesis_confidence emitted must be numeric (float or int)."""
        mem = make_expanded_habit(default_confidence=0.6)
        basket = {
            "delta": "response latency spiked",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("hypothesis_confidence")
        assert confidence is not None
        # payload stores as string "0.6"; node_executor passes through as-is
        # (string is acceptable — downstream callers cast to float)
        assert float(confidence) == pytest.approx(0.6)

    def test_default_confidence_is_0_6(self):
        """Default confidence for HYPOTHESIZE is 0.6."""
        mem = make_expanded_habit()  # uses default 0.6
        basket = {
            "delta": "unexpected silence",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        execute_node(mem, "__entry__", basket)

        confidence = basket.get("hypothesis_confidence")
        assert float(confidence) == pytest.approx(0.6)

    def test_stopif_fires_on_absent_delta(self):
        """Absent delta triggers STOPIF — no writes to hypothesis."""
        mem = make_expanded_habit()
        basket = {"twm_loaded": True, "time_direction": "backward"}  # delta absent
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("hypothesis") is None
        assert basket.get("hypothesis_confidence") is None

    def test_stopif_fires_on_null_delta(self):
        """Explicit None delta also triggers STOPIF."""
        mem = make_expanded_habit()
        basket = {"delta": None, "twm_loaded": True, "time_direction": "backward"}
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("hypothesis") is None

    def test_no_forkif_spawn_when_no_next_node_slot(self):
        """FORKIF must not spawn when next_node slot was not provided (renders as 'None')."""
        mem = make_expanded_habit()  # next_node=None → target bakes as "None"
        basket = {
            "delta": "latency spike",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.spawned == []

    def test_forkif_spawns_when_next_node_slot_provided(self):
        """FORKIF spawns when next_node was set at expansion time and hypothesis was formed."""
        mem = make_expanded_habit(next_node="MAIN_CONSTRAIN")
        basket = {
            "delta": "user confused by response",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "MAIN_CONSTRAIN" in result.spawned

    def test_forkif_target_is_literal_node_id(self):
        """FORKIF target must be a literal node ID string, not a Jinja2 placeholder."""
        mem = make_expanded_habit(next_node="TEST_CONSTRAIN")
        basket = {
            "delta": "plan diverged",
            "twm_loaded": True,
            "time_direction": "forward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert "TEST_CONSTRAIN" in result.spawned
        assert "{{ next_node }}" not in result.spawned

    def test_execution_stops_at_endif(self):
        """Normal execution (delta present, no fork) stops at ENDIF."""
        mem = make_expanded_habit()
        basket = {
            "delta": "unexpected token in output",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert result.stopped_by in ("ENDIF", "implicit_end", "FORKIF")

    def test_unknown_trigger_is_noop(self):
        """Firing an unknown trigger returns an empty result."""
        mem = make_expanded_habit()
        basket = {"delta": "anything", "twm_loaded": True, "time_direction": "backward"}
        result = execute_node(mem, "nonexistent_trigger", basket)

        assert result.instructions_run == 0
        assert basket.get("hypothesis") is None

    def test_hypothesis_defaults_to_delta_passthrough(self):
        """Default scaffold: hypothesis == delta (identity default — scaffold pass-through)."""
        mem = make_expanded_habit()
        delta = "observed: process exited unexpectedly"
        basket = {
            "delta": delta,
            "twm_loaded": True,
            "time_direction": "backward",
        }
        execute_node(mem, "__entry__", basket)

        assert (
            basket.get("hypothesis") == delta
        ), "Scaffold identity default: hypothesis should equal delta until code_ref is installed"

    def test_forward_time_direction_executes_normally(self):
        """forward time_direction does not break scaffold execution (code_ref handles branching)."""
        mem = make_expanded_habit(next_node="RISK_CONSTRAIN")
        basket = {
            "delta": "deployment approaching production",
            "twm_loaded": True,
            "time_direction": "forward",  # anticipatory / risk scan mode
        }
        result = execute_node(mem, "__entry__", basket)

        # Scaffold executes normally regardless of time_direction
        assert basket.get("hypothesis") is not None
        assert "RISK_CONSTRAIN" in result.spawned

    def test_chained_after_observe(self):
        """HYPOTHESIZE correctly reads basket.delta set by a prior OBSERVE node."""
        mem = make_expanded_habit(next_node="MAIN_CONSTRAIN")
        # Simulate OBSERVE having already written delta to basket
        basket = {
            "observed": "response was slow",
            "expected": "response in < 500ms",
            "delta": "response took 2400ms — 4.8× expected",
            "twm_loaded": True,
            "time_direction": "backward",
        }
        result = execute_node(mem, "__entry__", basket)

        assert basket.get("hypothesis") is not None
        assert "hypothesis_confidence" in basket
        assert "MAIN_CONSTRAIN" in result.spawned


# ── 7. D298 metadata: ANTICIPATE collapse ────────────────────────────────────


class TestD298AnticipateCollapse:
    """Verify D298 design is correctly encoded in metadata."""

    def test_why_field_mentions_d298(self):
        """why field must reference D298 (HYPOTHESIZE collapses ANTICIPATE)."""
        why = TEMPLATE_NODE["metadata"]["why"]
        assert (
            "D298" in why or "d298" in why.lower()
        ), "TEMPLATE_NODE.metadata.why must reference D298"

    def test_why_field_mentions_anticipate_collapse(self):
        """why field must mention ANTICIPATE being collapsed into HYPOTHESIZE."""
        why = TEMPLATE_NODE["metadata"]["why"]
        assert (
            "ANTICIPATE" in why or "anticipat" in why.lower()
        ), "why must explain that ANTICIPATE is collapsed into HYPOTHESIZE (D298)"

    def test_tags_include_d298(self):
        """Tags must include d298 marker."""
        tags = TEMPLATE_NODE["metadata"]["tags"]
        assert "d298" in tags or "D298" in tags, "tags must include 'd298'"

    def test_tags_include_predictive_coding(self):
        """Tags must include predictive_coding — D298 is grounded in predictive coding theory."""
        tags = TEMPLATE_NODE["metadata"]["tags"]
        assert any(
            "predictive" in t for t in tags
        ), "tags must include predictive_coding or similar"

    def test_expansion_why_mentions_predictive_coding(self):
        """Expanded habit why field must reference predictive coding / D298."""
        why = TEMPLATE_SCHEMA["expansion_schema"][0]["metadata"]["why"]
        assert (
            "D298" in why or "predictive" in why.lower()
        ), "expansion metadata.why must reference D298 / predictive coding"


# ── 8. JSON serialisability of the template node ─────────────────────────────


class TestJsonSerialisability:
    """The template node metadata must round-trip through json.dumps/loads."""

    def test_metadata_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_NODE["metadata"])
        loaded = json.loads(dumped)
        assert loaded["template"] is True
        assert loaded["layer"] == 3
        assert loaded["template_schema"]["pattern_name"] == "HYPOTHESIZE"

    def test_expansion_payload_is_json_serialisable(self):
        payload = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"]
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert isinstance(loaded["hypothesize_cell"], list)
        assert loaded["hypothesize_cell"][-1] == "ENDIF"

    def test_template_schema_is_json_serialisable(self):
        dumped = json.dumps(TEMPLATE_SCHEMA)
        loaded = json.loads(dumped)
        assert loaded["basket_contract"]["reads"] == [
            "delta",
            "twm_loaded",
            "time_direction",
        ]
        assert "hypothesis" in loaded["basket_contract"]["writes"]
        assert "hypothesis_confidence" in loaded["basket_contract"]["writes"]


# ── 9. TWM EMITIF opcode check (D300) ────────────────────────────────────────


class TestTwmEmitif:
    """Verify the cognitive_milieu EMITIF instruction exists in hypothesize_cell (D300)."""

    def setup_method(self):
        self.cell = TEMPLATE_SCHEMA["expansion_schema"][0]["payload"][
            "hypothesize_cell"
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
        assert found, "No EMITIF targeting cognitive_milieu in hypothesize_cell"

    def test_cognitive_milieu_emitif_key_is_hypothesis(self):
        """The cognitive_milieu EMITIF must write key 'HYPOTHESIS' (D300)."""
        found = any(
            isinstance(instr, list)
            and len(instr) == 5
            and instr[0] == "EMITIF"
            and instr[2] == "HYPOTHESIS"
            and instr[4] == "cognitive_milieu"
            for instr in self.cell
        )
        assert found, "No EMITIF writing HYPOTHESIS to cognitive_milieu"

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
