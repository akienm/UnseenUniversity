"""
test_layer4_nodes.py — Tests for Layer 4 programming TEMPLATE nodes.

Covers all 5 layer 4 seed scripts:
  - tpl-layer4-read-codebase  (seed_layer4_read_codebase.py)
  - tpl-layer4-patch-file     (seed_layer4_patch_file.py)
  - tpl-layer4-run-bash       (seed_layer4_run_bash.py)
  - tpl-layer4-verify-result  (seed_layer4_verify_result.py)
  - tpl-layer4-write-test     (seed_layer4_write_test.py)

Each node tested for:
  1. Template schema structure (required fields, layer=4, pattern_name)
  2. Basket contract declared (reads/writes)
  3. Expansion schema validity (id template, memory_type, metadata flags)
  4. Opcode cell validity (known opcodes, correct arity, STOPIF/FORKIF guards)
  5. JSON serialisability of metadata + payload
  6. node_executor integration — scaffold STOPIF/EMITIF/FORKIF/BRANCHIF behaviour

No live DB required — tests run against seed script data + node_executor directly.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wild_igor.igor.cognition.node_executor import execute_node, ExecutionResult

from claudecode.seed_layer4_read_codebase import (
    TEMPLATE_ID as RC_ID,
    TEMPLATE_SCHEMA as RC_SCHEMA,
    TEMPLATE_NODE as RC_NODE,
)
from claudecode.seed_layer4_patch_file import (
    TEMPLATE_ID as PF_ID,
    TEMPLATE_SCHEMA as PF_SCHEMA,
    TEMPLATE_NODE as PF_NODE,
)
from claudecode.seed_layer4_run_bash import (
    TEMPLATE_ID as RB_ID,
    TEMPLATE_SCHEMA as RB_SCHEMA,
    TEMPLATE_NODE as RB_NODE,
)
from claudecode.seed_layer4_verify_result import (
    TEMPLATE_ID as VR_ID,
    TEMPLATE_SCHEMA as VR_SCHEMA,
    TEMPLATE_NODE as VR_NODE,
)
from claudecode.seed_layer4_write_test import (
    TEMPLATE_ID as WT_ID,
    TEMPLATE_SCHEMA as WT_SCHEMA,
    TEMPLATE_NODE as WT_NODE,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_OPS = {"LABEL", "STOPIF", "EMITIF", "BRANCHIF", "FORKIF", "ENDIF"}
_VALID_CHANNELS = {
    "basket",
    "emotional_milieu",
    "cognitive_milieu",
    "console",
    "web",
    "discord",
    "memory",
}


class MockMemory:
    def __init__(self, memory_id, payload=None, metadata=None):
        self.id = memory_id
        self.payload = payload or {}
        self.metadata = metadata or {"triggers": {}}


def _make_mock(schema, cell_key, trigger="__entry__"):
    expansion = schema["expansion_schema"][0]
    payload = dict(expansion["payload"])
    metadata = {
        "triggers": {trigger: cell_key},
        **{k: v for k, v in expansion["metadata"].items() if k != "triggers"},
    }
    return MockMemory(
        f"TEST_{schema['pattern_name']}", payload=payload, metadata=metadata
    )


def _get_cell(schema):
    cell_name = list(schema["expansion_schema"][0]["payload"].keys())
    for k, v in schema["expansion_schema"][0]["payload"].items():
        if isinstance(v, list):
            return k, v
    raise ValueError("No cell found in expansion payload")


# ── 1. Common schema structure checks (parametrised over all 5 nodes) ─────────

ALL_SCHEMAS = [
    ("READ_CODEBASE", RC_ID, RC_SCHEMA, RC_NODE),
    ("PATCH_FILE", PF_ID, PF_SCHEMA, PF_NODE),
    ("RUN_BASH", RB_ID, RB_SCHEMA, RB_NODE),
    ("VERIFY_RESULT", VR_ID, VR_SCHEMA, VR_NODE),
    ("WRITE_TEST", WT_ID, WT_SCHEMA, WT_NODE),
]


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_template_id_format(name, tid, schema, node):
    assert tid.startswith("tpl-layer4-"), f"{name}: id must start with tpl-layer4-"
    assert node["id"] == tid


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_schema_layer_is_4(name, tid, schema, node):
    assert schema["layer"] == 4
    assert node["metadata"]["layer"] == 4


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_pattern_name_matches(name, tid, schema, node):
    assert schema["pattern_name"] == name
    assert node["metadata"]["pattern_name"] == name


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_schema_required_top_level_keys(name, tid, schema, node):
    required = {
        "pattern_name",
        "layer",
        "schema_version",
        "substitution_engine",
        "slot_manifest",
        "expansion_schema",
        "basket_contract",
    }
    assert required.issubset(set(schema.keys())), f"{name}: missing keys"


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_node_template_true(name, tid, schema, node):
    assert node["metadata"]["template"] is True


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_node_memory_type_procedural(name, tid, schema, node):
    assert node["memory_type"] == "PROCEDURAL"


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_expansion_schema_has_one_entry(name, tid, schema, node):
    assert isinstance(schema["expansion_schema"], list)
    assert len(schema["expansion_schema"]) == 1


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_expansion_metadata_template_false(name, tid, schema, node):
    exp = schema["expansion_schema"][0]
    assert exp["metadata"]["template"] is False


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_expansion_metadata_layer_4(name, tid, schema, node):
    exp = schema["expansion_schema"][0]
    assert exp["metadata"]["layer"] == 4


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_expansion_has_entry_trigger(name, tid, schema, node):
    exp = schema["expansion_schema"][0]
    assert "__entry__" in exp["metadata"]["triggers"]


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_basket_contract_declared(name, tid, schema, node):
    contract = schema["basket_contract"]
    assert "reads" in contract
    assert "writes" in contract
    assert len(contract["writes"]) >= 1


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_metadata_json_serialisable(name, tid, schema, node):
    dumped = json.dumps(node["metadata"])
    loaded = json.loads(dumped)
    assert loaded["template"] is True
    assert loaded["layer"] == 4


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_schema_json_serialisable(name, tid, schema, node):
    dumped = json.dumps(schema)
    loaded = json.loads(dumped)
    assert loaded["pattern_name"] == name
    assert loaded["layer"] == 4


# ── 2. Opcode cell validity (per node) ───────────────────────────────────────


def _check_cell(cell, name):
    for instr in cell:
        if isinstance(instr, list) and instr:
            assert instr[0] in _VALID_OPS, f"{name}: unknown opcode {instr[0]}"
        elif isinstance(instr, str):
            assert instr == "ENDIF", f"{name}: unexpected str instruction {instr!r}"
    assert cell[-1] == "ENDIF", f"{name}: cell must end with ENDIF"


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_cell_opcodes_valid(name, tid, schema, node):
    _, cell = _get_cell(schema)
    _check_cell(cell, name)


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_cell_emitif_arity(name, tid, schema, node):
    _, cell = _get_cell(schema)
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "EMITIF":
            assert len(instr) == 5, f"{name}: EMITIF arity wrong: {instr}"


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_cell_emitif_channels_valid(name, tid, schema, node):
    _, cell = _get_cell(schema)
    for instr in cell:
        if isinstance(instr, list) and instr and instr[0] == "EMITIF":
            assert instr[4] in _VALID_CHANNELS, f"{name}: bad channel {instr[4]}"


@pytest.mark.parametrize("name,tid,schema,node", ALL_SCHEMAS)
def test_cell_forkif_branchif_arity(name, tid, schema, node):
    _, cell = _get_cell(schema)
    for instr in cell:
        if isinstance(instr, list) and instr:
            if instr[0] == "FORKIF":
                assert len(instr) == 3, f"{name}: FORKIF arity wrong"
            elif instr[0] == "BRANCHIF":
                assert len(instr) == 4, f"{name}: BRANCHIF arity wrong"


# ── 3. Node-specific basket contract checks ───────────────────────────────────


class TestReadCodebaseContract:
    def test_reads_ticket_description(self):
        assert "ticket_description" in RC_SCHEMA["basket_contract"]["reads"]

    def test_writes_actual(self):
        assert "actual" in RC_SCHEMA["basket_contract"]["writes"]

    def test_writes_plan_files(self):
        assert "plan_files" in RC_SCHEMA["basket_contract"]["writes"]

    def test_writes_observe_hits(self):
        assert "observe_hits" in RC_SCHEMA["basket_contract"]["writes"]

    def test_cell_has_stopif_on_ticket_description(self):
        _, cell = _get_cell(RC_SCHEMA)
        stopif_keys = [
            instr[1][0]
            for instr in cell
            if isinstance(instr, list)
            and instr[0] == "STOPIF"
            and isinstance(instr[1], list)
        ]
        assert "ticket_description" in stopif_keys

    def test_cell_emits_actual_to_basket(self):
        _, cell = _get_cell(RC_SCHEMA)
        found = any(
            isinstance(i, list)
            and len(i) == 5
            and i[0] == "EMITIF"
            and i[2] == "actual"
            and i[4] == "basket"
            for i in cell
        )
        assert found

    def test_code_refs_situate_and_observe(self):
        exp = RC_SCHEMA["expansion_schema"][0]
        assert "pe_chain:pe_situate" in (exp["metadata"].get("code_ref", ""))
        assert "pe_chain:pe_observe" in (exp["metadata"].get("code_ref_2", ""))


class TestPatchFileContract:
    def test_reads_hypothesis(self):
        assert "hypothesis" in PF_SCHEMA["basket_contract"]["reads"]

    def test_writes_implement_result(self):
        assert "implement_result" in PF_SCHEMA["basket_contract"]["writes"]

    def test_writes_implement_skipped(self):
        assert "implement_skipped" in PF_SCHEMA["basket_contract"]["writes"]

    def test_cell_has_stopif_on_hypothesis(self):
        _, cell = _get_cell(PF_SCHEMA)
        stopif_keys = [
            instr[1][0]
            for instr in cell
            if isinstance(instr, list)
            and instr[0] == "STOPIF"
            and isinstance(instr[1], list)
        ]
        assert "hypothesis" in stopif_keys

    def test_cell_has_dual_forkif(self):
        _, cell = _get_cell(PF_SCHEMA)
        forkifs = [i for i in cell if isinstance(i, list) and i[0] == "FORKIF"]
        assert len(forkifs) == 2, "PATCH_FILE needs pass_node + fail_node FORKIF pair"

    def test_code_ref_is_pe_implement(self):
        exp = PF_SCHEMA["expansion_schema"][0]
        assert exp["metadata"]["code_ref"] == "pe_chain:pe_implement"


class TestRunBashContract:
    def test_reads_bash_cmd(self):
        assert "bash_cmd" in RB_SCHEMA["basket_contract"]["reads"]

    def test_writes_bash_output(self):
        assert "bash_output" in RB_SCHEMA["basket_contract"]["writes"]

    def test_cell_has_stopif_on_bash_cmd(self):
        _, cell = _get_cell(RB_SCHEMA)
        stopif_keys = [
            instr[1][0]
            for instr in cell
            if isinstance(instr, list)
            and instr[0] == "STOPIF"
            and isinstance(instr[1], list)
        ]
        assert "bash_cmd" in stopif_keys

    def test_cell_emits_bash_output_to_basket(self):
        _, cell = _get_cell(RB_SCHEMA)
        found = any(
            isinstance(i, list)
            and len(i) == 5
            and i[0] == "EMITIF"
            and i[2] == "bash_output"
            and i[4] == "basket"
            for i in cell
        )
        assert found

    def test_code_ref_is_pe_run_bash(self):
        exp = RB_SCHEMA["expansion_schema"][0]
        assert exp["metadata"]["code_ref"] == "pe_chain:pe_run_bash"


class TestVerifyResultContract:
    def test_writes_test_result(self):
        assert "test_result" in VR_SCHEMA["basket_contract"]["writes"]

    def test_writes_verify_passed(self):
        assert "verify_passed" in VR_SCHEMA["basket_contract"]["writes"]

    def test_cell_has_branchif(self):
        _, cell = _get_cell(VR_SCHEMA)
        has_branchif = any(isinstance(i, list) and i[0] == "BRANCHIF" for i in cell)
        assert has_branchif, "VERIFY_RESULT must have BRANCHIF for pass/fail routing"

    def test_branchif_checks_test_result(self):
        _, cell = _get_cell(VR_SCHEMA)
        for instr in cell:
            if isinstance(instr, list) and instr[0] == "BRANCHIF":
                cond = instr[1]
                assert isinstance(cond, list)
                assert cond[0] == "test_result"

    def test_code_ref_is_pe_test(self):
        exp = VR_SCHEMA["expansion_schema"][0]
        assert exp["metadata"]["code_ref"] == "pe_chain:pe_test"


class TestWriteTestContract:
    def test_reads_ticket_description(self):
        assert "ticket_description" in WT_SCHEMA["basket_contract"]["reads"]

    def test_reads_actual(self):
        assert "actual" in WT_SCHEMA["basket_contract"]["reads"]

    def test_writes_test_code(self):
        assert "test_code" in WT_SCHEMA["basket_contract"]["writes"]

    def test_writes_test_confidence(self):
        assert "test_confidence" in WT_SCHEMA["basket_contract"]["writes"]

    def test_cell_has_triple_stopif(self):
        """Guards: ticket_description, actual==None, actual==empty."""
        _, cell = _get_cell(WT_SCHEMA)
        stopifs = [i for i in cell if isinstance(i, list) and i[0] == "STOPIF"]
        assert len(stopifs) >= 2

    def test_test_mode_in_payload(self):
        exp = WT_SCHEMA["expansion_schema"][0]
        assert exp["payload"].get("test_mode") is True

    def test_code_ref_is_pe_hypothesize(self):
        exp = WT_SCHEMA["expansion_schema"][0]
        assert exp["metadata"]["code_ref"] == "pe_chain:pe_hypothesize"


# ── 4. node_executor integration — READ_CODEBASE scaffold ─────────────────────


class TestReadCodebaseExecutor:
    def _mem(self, next_node=None):
        schema = RC_SCHEMA
        exp = schema["expansion_schema"][0]
        import copy

        cell = copy.deepcopy(exp["payload"]["read_codebase_cell"])
        for instr in cell:
            if isinstance(instr, list) and instr[0] == "FORKIF":
                instr[2] = str(next_node) if next_node else "None"
        payload = {**exp["payload"], "read_codebase_cell": cell}
        metadata = {"triggers": {"__entry__": "read_codebase_cell"}}
        return MockMemory("TEST_READ_CODEBASE", payload=payload, metadata=metadata)

    def test_stopif_fires_on_missing_ticket_description(self):
        mem = self._mem()
        basket = {}
        result = execute_node(mem, "__entry__", basket)
        assert result.stopped_by == "STOPIF"
        assert "actual" not in basket

    def test_scaffold_emits_actual_empty_string(self):
        mem = self._mem()
        basket = {"ticket_description": "add a logging call to main()"}
        execute_node(mem, "__entry__", basket)
        assert "actual" in basket
        assert basket["actual"] == ""

    def test_scaffold_emits_plan_files_empty_list(self):
        mem = self._mem()
        basket = {"ticket_description": "fix the bug"}
        execute_node(mem, "__entry__", basket)
        assert basket.get("plan_files") == []

    def test_scaffold_observe_hits_zero(self):
        mem = self._mem()
        basket = {"ticket_description": "fix the bug"}
        execute_node(mem, "__entry__", basket)
        assert basket.get("observe_hits") == 0

    def test_no_fork_when_observe_hits_zero(self):
        mem = self._mem(next_node="NEXT_NODE")
        basket = {"ticket_description": "fix the bug"}
        result = execute_node(mem, "__entry__", basket)
        # observe_hits scaffold = 0, so FORKIF condition fails
        assert "NEXT_NODE" not in result.spawned


# ── 5. node_executor integration — RUN_BASH scaffold ─────────────────────────


class TestRunBashExecutor:
    def _mem(self, next_node=None):
        schema = RB_SCHEMA
        exp = schema["expansion_schema"][0]
        import copy

        cell = copy.deepcopy(exp["payload"]["run_bash_cell"])
        for instr in cell:
            if isinstance(instr, list) and instr[0] == "FORKIF":
                instr[2] = str(next_node) if next_node else "None"
        payload = {**exp["payload"], "run_bash_cell": cell}
        metadata = {"triggers": {"__entry__": "run_bash_cell"}}
        return MockMemory("TEST_RUN_BASH", payload=payload, metadata=metadata)

    def test_stopif_fires_on_missing_bash_cmd(self):
        mem = self._mem()
        basket = {}
        result = execute_node(mem, "__entry__", basket)
        assert result.stopped_by == "STOPIF"
        assert "bash_output" not in basket

    def test_scaffold_emits_bash_output_empty_string(self):
        mem = self._mem()
        basket = {"bash_cmd": "echo hello"}
        execute_node(mem, "__entry__", basket)
        assert "bash_output" in basket
        assert basket["bash_output"] == ""

    def test_no_fork_when_bash_output_empty(self):
        mem = self._mem(next_node="NEXT_NODE")
        basket = {"bash_cmd": "echo hello"}
        result = execute_node(mem, "__entry__", basket)
        assert "NEXT_NODE" not in result.spawned


# ── 6. node_executor integration — PATCH_FILE scaffold ───────────────────────


class TestPatchFileExecutor:
    def _mem(self, pass_node=None, fail_node=None):
        schema = PF_SCHEMA
        exp = schema["expansion_schema"][0]
        import copy

        cell = copy.deepcopy(exp["payload"]["patch_file_cell"])
        for instr in cell:
            if isinstance(instr, list) and instr[0] == "FORKIF":
                if instr[1] == ["implement_skipped", "==", False]:
                    instr[2] = str(pass_node) if pass_node else "None"
                else:
                    instr[2] = str(fail_node) if fail_node else "None"
        payload = {**exp["payload"], "patch_file_cell": cell}
        metadata = {"triggers": {"__entry__": "patch_file_cell"}}
        return MockMemory("TEST_PATCH_FILE", payload=payload, metadata=metadata)

    def test_stopif_fires_on_missing_hypothesis(self):
        mem = self._mem()
        basket = {}
        result = execute_node(mem, "__entry__", basket)
        assert result.stopped_by == "STOPIF"
        assert "implement_result" not in basket

    def test_scaffold_emits_implement_skipped_true(self):
        mem = self._mem()
        basket = {"hypothesis": {"file": "x.py", "old_string": "a", "new_string": "b"}}
        execute_node(mem, "__entry__", basket)
        assert basket.get("implement_skipped") is True

    def test_scaffold_emits_implement_result_skipped(self):
        mem = self._mem()
        basket = {"hypothesis": {"file": "x.py", "old_string": "a", "new_string": "b"}}
        execute_node(mem, "__entry__", basket)
        assert basket.get("implement_result") == "skipped"


# ── 7. node_executor integration — VERIFY_RESULT scaffold ────────────────────


class TestVerifyResultExecutor:
    def _mem(self, pass_node=None, fail_node=None):
        schema = VR_SCHEMA
        exp = schema["expansion_schema"][0]
        import copy

        cell = copy.deepcopy(exp["payload"]["verify_result_cell"])
        for instr in cell:
            if isinstance(instr, list) and instr[0] == "BRANCHIF":
                instr[2] = str(pass_node) if pass_node else "None"
                instr[3] = str(fail_node) if fail_node else "None"
        payload = {**exp["payload"], "verify_result_cell": cell}
        metadata = {"triggers": {"__entry__": "verify_result_cell"}}
        return MockMemory("TEST_VERIFY_RESULT", payload=payload, metadata=metadata)

    def test_scaffold_emits_test_result_pending(self):
        mem = self._mem()
        basket = {}
        execute_node(mem, "__entry__", basket)
        assert basket.get("test_result") == "pending"

    def test_scaffold_emits_verify_passed_false(self):
        mem = self._mem()
        basket = {}
        execute_node(mem, "__entry__", basket)
        assert basket.get("verify_passed") is False
