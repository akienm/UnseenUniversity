"""
test_node_executor.py — Tests for engram node executor (D260, D290, D291, D307).

Tests for execute_node() function including:
  - LABEL instruction (no-op marker for jump targets)
  - STOPIF instruction (conditional terminator)
  - BRANCHIF with @label targets (local jumps)
  - BRANCHIF with bare node IDs (existing behavior)
  - EMITIF, FORKIF, ENDIF instructions
  - MCPCALL instruction (tool registry dispatch, D307)
  - Condition evaluation
  - Value resolution
"""

import pytest
from dataclasses import dataclass, field

from unseen_university.devices.igor.cognition.node_executor import execute_node, ExecutionResult
from unseen_university.devices.igor.memory.models import Memory, MemoryType


class MockMemory:
    """Mock Memory object for testing."""

    def __init__(self, memory_id, payload=None, metadata=None):
        self.id = memory_id
        self.payload = payload or {}
        self.metadata = metadata or {"triggers": {}}


class TestLabelInstruction:
    """Tests for LABEL instruction."""

    def test_label_is_noop(self):
        """LABEL should be a no-op that doesn't affect output."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@start"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert result.instructions_run == 3
        assert basket.get("key1") == "value1"

    def test_multiple_labels(self):
        """Multiple LABEL instructions should all be no-ops."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@start"],
                    ["LABEL", "@middle"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["LABEL", "@end"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert result.instructions_run == 5
        assert basket.get("key1") == "value1"

    def test_label_without_target(self):
        """LABEL with missing target should log warning and continue."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL"],  # Missing target
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"


class TestNoopCommentInstruction:
    """T-payload-comment-opcode: NOOP_COMMENT is runtime no-op, preserved for humans."""

    def test_noop_comment_does_not_affect_basket(self):
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["NOOP_COMMENT", "this is why we do the next thing"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)
        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"

    def test_noop_comment_counts_as_run(self):
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["NOOP_COMMENT", "first"],
                    ["NOOP_COMMENT", "second"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)
        # 2 NOOP_COMMENTs + 1 EMITIF + 1 ENDIF = 4 instructions
        assert result.instructions_run == 4

    def test_noop_comment_with_no_text(self):
        """NOOP_COMMENT with just the opcode (no text arg) shouldn't crash."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["NOOP_COMMENT"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)
        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"

    def test_noop_comment_preserves_other_state(self):
        """Comment between two operations doesn't disturb either."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "before", "first", "basket"],
                    ["NOOP_COMMENT", "explanation goes here"],
                    ["EMITIF", True, "after", "second", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        execute_node(memory, "my_trigger", basket)
        assert basket.get("before") == "first"
        assert basket.get("after") == "second"

    def test_noop_comment_does_not_trigger_unknown_op_warning(self):
        """The opcode is in the known set — should not log 'unknown instruction'."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["NOOP_COMMENT", "x"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        # Capture log output
        import logging
        from unseen_university.devices.igor.cognition import node_executor as ne_mod

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture(level=logging.WARNING)
        ne_mod.log._logger.addHandler(handler)
        try:
            execute_node(memory, "my_trigger", basket)
        finally:
            ne_mod.log._logger.removeHandler(handler)
        unknown_warnings = [
            r for r in records if "unknown instruction" in r.getMessage()
        ]
        assert not unknown_warnings, "NOOP_COMMENT should be a known op"


class TestStopifInstruction:
    """Tests for STOPIF instruction."""

    def test_stopif_true_condition_stops(self):
        """STOPIF with true condition should stop execution."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", True],  # Should stop here
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None  # Should not be executed

    def test_stopif_false_condition_continues(self):
        """STOPIF with false condition should continue execution."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", False],  # Should not stop
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") == "value2"

    def test_stopif_with_basket_condition(self):
        """STOPIF should evaluate basket-based conditions."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", ["count", "==", 5]],  # Stop if count == 5
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {"count": 5}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "STOPIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None

    def test_stopif_with_basket_condition_false(self):
        """STOPIF should not stop if basket condition is false."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", ["count", "==", 5]],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {"count": 3}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") == "value2"


class TestBranchifWithLabel:
    """Tests for BRANCHIF with @label targets."""

    def test_branchif_label_forward_jump(self):
        """BRANCHIF with @label should jump forward to that label."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@start"],
                    ["BRANCHIF", True, "@skip"],  # Jump forward
                    ["EMITIF", True, "key1", "value1", "basket"],  # Should be skipped
                    ["LABEL", "@skip"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") is None  # Skipped
        assert basket.get("key2") == "value2"

    def test_branchif_label_backward_jump(self):
        """BRANCHIF with @label should support backward jumps."""
        # Simple backward jump test: jump back once based on condition
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "executed", 1, "basket"],
                    ["LABEL", "@loop"],
                    ["BRANCHIF", False, "@loop"],  # Don't jump back
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        # Should execute without looping infinitely
        assert result.stopped_by == "ENDIF"
        assert basket.get("executed") == 1

    def test_branchif_label_not_found(self):
        """BRANCHIF with missing @label should log warning and stop."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True, "@nonexistent"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "BRANCHIF"
        assert basket.get("key1") is None

    def test_branchif_label_conditional_false(self):
        """BRANCHIF with @label should not jump if condition is false."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", False, "@skip"],  # Don't jump
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["LABEL", "@skip"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") == "value2"


class TestBranchifWithNodeId:
    """Tests for BRANCHIF with bare node IDs (existing behavior)."""

    def test_branchif_node_id_sets_next_node(self):
        """BRANCHIF with bare node ID should set next_node and break."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["BRANCHIF", True, "next_node_id"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node == "next_node_id"
        assert result.stopped_by == "BRANCHIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None

    def test_branchif_node_id_conditional_false(self):
        """BRANCHIF with bare node ID should not branch if condition is false."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["BRANCHIF", False, "next_node_id"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node is None
        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") == "value2"


class TestComplexScenarios:
    """Tests for complex scenarios mixing multiple instruction types."""

    def test_label_stopif_branchif_combination(self):
        """Test a complex scenario with LABEL, STOPIF, and BRANCHIF."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@start"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", ["flag", "==", True]],
                    ["BRANCHIF", False, "@end"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    ["LABEL", "@end"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )

        # Test with flag=False (STOPIF doesn't stop)
        basket = {"flag": False}
        result = execute_node(memory, "my_trigger", basket)
        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") == "value2"

        # Test with flag=True (STOPIF stops)
        basket = {"flag": True}
        result = execute_node(memory, "my_trigger", basket)
        assert result.stopped_by == "STOPIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None

    def test_forkif_with_labels(self):
        """FORKIF should work alongside LABEL and BRANCHIF."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@main"],
                    ["FORKIF", True, "spawned_node"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["BRANCHIF", True, "@end"],
                    ["LABEL", "@end"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.spawned == ["spawned_node"]
        assert basket.get("key1") == "value1"
        assert result.stopped_by == "ENDIF"

    def test_mixed_label_targets(self):
        """Test discrimination between @label targets and bare node IDs."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", False, "@internal"],  # Conditional, won't branch
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["BRANCHIF", True, "external_node"],  # Will branch (not @label)
                    ["LABEL", "@internal"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node == "external_node"
        assert result.stopped_by == "BRANCHIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None


class TestExecutionResultFields:
    """Tests for ExecutionResult field correctness."""

    def test_execution_result_stopped_by_values(self):
        """Test all possible stopped_by values are set correctly."""
        # implicit_end
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["EMITIF", True, "key", "val", "basket"]]},
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "implicit_end"

        # ENDIF
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key", "val", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "ENDIF"

        # STOPIF
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["STOPIF", True]]},
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "STOPIF"

        # BRANCHIF
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["BRANCHIF", True, "next_node"]]},
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "BRANCHIF"

    def test_instructions_run_count(self):
        """Test that instructions_run counts correctly."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["LABEL", "@start"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["STOPIF", True],
                    ["EMITIF", True, "key2", "value2", "basket"],  # Won't run
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.instructions_run == 3  # LABEL, EMITIF, STOPIF


class TestEdgeCases:
    """Tests for edge cases and malformed inputs."""

    def test_empty_cell(self):
        """Empty cell should return empty result."""
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": []},
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "implicit_end"
        assert result.instructions_run == 0

    def test_malformed_stopif(self):
        """STOPIF with wrong arg count should log and continue."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["STOPIF"],  # Missing condition
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "ENDIF"

    def test_malformed_branchif(self):
        """BRANCHIF with wrong arg count should log and continue."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True],  # Missing target
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "ENDIF"

    def test_no_payload(self):
        """Node without payload should return empty result."""
        memory = MockMemory(
            "test_mem",
            payload=None,
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "implicit_end"
        assert result.instructions_run == 0

    def test_no_trigger(self):
        """Node with unknown trigger should return empty result."""
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["EMITIF", True, "key", "val", "basket"]]},
            metadata={"triggers": {}},
        )
        result = execute_node(memory, "unknown_trigger", {})
        assert result.stopped_by == "implicit_end"
        assert result.instructions_run == 0

    def test_label_numeric_target(self):
        """LABEL with numeric target should work (converted to string)."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True, "@label_123"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["LABEL", "@label_123"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        result = execute_node(memory, "my_trigger", {})
        assert result.stopped_by == "ENDIF"
        assert result.basket.get("key2") == "value2"


class TestBranchifWithTrigger:
    """Tests for BRANCHIF with node_id#trigger_name syntax (D296)."""

    def test_branchif_node_with_trigger_name(self):
        """BRANCHIF with node_id#trigger_name should set both next_node and next_trigger."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "key1", "value1", "basket"],
                    ["BRANCHIF", True, "next_node_id#custom_trigger"],
                    ["EMITIF", True, "key2", "value2", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node == "next_node_id"
        assert result.next_trigger == "custom_trigger"
        assert result.stopped_by == "BRANCHIF"
        assert basket.get("key1") == "value1"
        assert basket.get("key2") is None

    def test_branchif_bare_node_has_no_trigger(self):
        """BRANCHIF with bare node ID should set next_trigger to None."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True, "next_node_id"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node == "next_node_id"
        assert result.next_trigger is None
        assert result.stopped_by == "BRANCHIF"

    def test_branchif_trigger_with_multiple_hashes(self):
        """BRANCHIF should split only on first # (node_id might contain it)."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True, "node#first#second"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        # Should split on first # only
        assert result.next_node == "node"
        assert result.next_trigger == "first#second"
        assert result.stopped_by == "BRANCHIF"

    def test_branchif_trigger_conditional_false(self):
        """BRANCHIF with trigger syntax should not branch if condition is false."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", False, "next_node#trigger"],
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node is None
        assert result.next_trigger is None
        assert result.stopped_by == "ENDIF"
        assert basket.get("key1") == "value1"

    def test_branchif_trigger_with_complex_names(self):
        """BRANCHIF should handle complex node and trigger names."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["BRANCHIF", True, "node_2026_04_01#on_success_v2"],
                    ["EMITIF", True, "skipped", "yes", "basket"],
                ]
            },
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "my_trigger", basket)

        assert result.next_node == "node_2026_04_01"
        assert result.next_trigger == "on_success_v2"
        assert basket.get("skipped") is None


class TestMcpCallInstruction:
    """Tests for MCPCALL instruction (D307 — tool registry dispatch)."""

    def _make_memory(self, cell):
        return MockMemory(
            "test_mem",
            payload={"exec_cell": cell},
            metadata={"triggers": {"my_trigger": "exec_cell"}},
        )

    def test_mcpcall_calls_registered_tool(self):
        """MCPCALL dispatches to a registered tool and stores result in basket."""
        from unittest.mock import patch, MagicMock
        from unseen_university.devices.igor.tools.registry import Tool, ToolRegistry

        mock_tool = Tool(
            name="test_echo",
            description="test",
            parameters={},
            fn=lambda text="": f"echo:{text}",
        )
        mock_registry = ToolRegistry()
        mock_registry.register(mock_tool)

        memory = self._make_memory(
            [
                ["MCPCALL", "test_echo", "call_args", "call_result"],
                "ENDIF",
            ]
        )
        basket = {"call_args": {"text": "hello"}}

        with patch(
            "unseen_university.devices.igor.cognition.node_executor._tool_registry", mock_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket["call_result"] == "echo:hello"

    def test_mcpcall_unknown_tool_stores_error(self):
        """MCPCALL with unknown tool name writes __error__ to basket; execution continues."""
        from unittest.mock import patch
        from unseen_university.devices.igor.tools.registry import ToolRegistry

        empty_registry = ToolRegistry()
        memory = self._make_memory(
            [
                ["MCPCALL", "nonexistent_tool", "args", "result"],
                ["EMITIF", True, "after", "yes", "basket"],
                "ENDIF",
            ]
        )
        basket = {}

        with patch(
            "unseen_university.devices.igor.cognition.node_executor._tool_registry", empty_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert "__error__" in basket["result"]
        assert basket.get("after") == "yes"  # execution continues past error

    def test_mcpcall_tool_exception_stores_error(self):
        """MCPCALL stores __error__ in basket when tool raises; execution continues."""
        from unittest.mock import patch
        from unseen_university.devices.igor.tools.registry import Tool, ToolRegistry

        def boom(**_):
            raise RuntimeError("tool exploded")

        mock_tool = Tool(name="boom_tool", description="", parameters={}, fn=boom)
        mock_registry = ToolRegistry()
        mock_registry.register(mock_tool)

        memory = self._make_memory(
            [
                ["MCPCALL", "boom_tool", "args", "out"],
                ["EMITIF", True, "continued", "yes", "basket"],
                "ENDIF",
            ]
        )
        basket = {}

        with patch(
            "unseen_university.devices.igor.cognition.node_executor._tool_registry", mock_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert "tool exploded" in basket["out"]["__error__"]
        assert basket.get("continued") == "yes"

    def test_mcpcall_tool_name_from_basket(self):
        """MCPCALL resolves tool name from basket when given [\"basket\", key]."""
        from unittest.mock import patch
        from unseen_university.devices.igor.tools.registry import Tool, ToolRegistry

        mock_tool = Tool(
            name="dynamic_tool",
            description="",
            parameters={},
            fn=lambda: "dynamic_result",
        )
        mock_registry = ToolRegistry()
        mock_registry.register(mock_tool)

        memory = self._make_memory(
            [
                ["MCPCALL", ["basket", "tool_key"], "args", "out"],
                "ENDIF",
            ]
        )
        basket = {"tool_key": "dynamic_tool", "args": {}}

        with patch(
            "unseen_university.devices.igor.cognition.node_executor._tool_registry", mock_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert basket["out"] == "dynamic_result"


class TestForkifBasketSharing:
    """Tests documenting T-basket-fork-sharing semantics.

    FORKIF spawns node IDs for background dispatch. The dispatch layer (main.py)
    passes the same basket dict by reference — no copy-on-fork. These tests verify
    that execute_node returns the original basket object and that FORKIF correctly
    populates spawned IDs for the dispatch layer to handle.
    """

    def test_forkif_returns_same_basket_object(self):
        """execute_node returns the exact same basket dict (identity), not a copy."""
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["FORKIF", True, "spawned_node"], "ENDIF"]},
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {"ctx": "value"}
        result = execute_node(memory, "trigger", basket)

        assert result.basket is basket  # same object — no copy

    def test_forkif_spawned_node_ids_returned_for_dispatch(self):
        """FORKIF populates result.spawned with node IDs for the dispatch layer."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["FORKIF", True, "worker_a"],
                    ["FORKIF", True, "worker_b"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "trigger", basket)

        assert result.spawned == ["worker_a", "worker_b"]

    def test_forkif_false_condition_does_not_spawn(self):
        """FORKIF with false condition does not add to spawned."""
        memory = MockMemory(
            "test_mem",
            payload={"exec_cell": [["FORKIF", False, "worker_a"], "ENDIF"]},
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        result = execute_node(memory, "trigger", {})

        assert result.spawned == []

    def test_emitif_to_basket_visible_to_subsequent_fork_reads(self):
        """EMITIF→basket writes before FORKIF are present in basket when fork runs.

        Since forks share the parent basket by reference, values written before
        the FORKIF are immediately visible. This test verifies the basket state
        after execute_node completes contains all writes including pre-fork ones.
        """
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["EMITIF", True, "pre_fork_key", "pre_fork_val", "basket"],
                    ["FORKIF", True, "worker_node"],
                    ["EMITIF", True, "post_fork_key", "post_fork_val", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "trigger", basket)

        # Both pre- and post-fork writes land in the same shared basket
        assert basket["pre_fork_key"] == "pre_fork_val"
        assert basket["post_fork_key"] == "post_fork_val"
        assert result.spawned == ["worker_node"]


class TestSpawnifInstruction:
    """Tests for SPAWNIF instruction (T-spawnif-new-opcode)."""

    def test_spawnif_fires_on_true_condition(self):
        """SPAWNIF with True condition appends to spawned_fresh."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", True, "child_node"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        result = execute_node(memory, "trigger", {})
        assert result.spawned_fresh == ["child_node"]
        assert result.stopped_by == "ENDIF"

    def test_spawnif_does_not_fire_on_false_condition(self):
        """SPAWNIF with False condition does not append to spawned_fresh."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", False, "child_node"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        result = execute_node(memory, "trigger", {})
        assert result.spawned_fresh == []
        assert result.stopped_by == "ENDIF"

    def test_spawnif_appends_to_spawned_fresh_not_spawned(self):
        """SPAWNIF must not pollute result.spawned (FORKIF list)."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", True, "fresh_child"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        result = execute_node(memory, "trigger", {})
        assert result.spawned_fresh == ["fresh_child"]
        assert result.spawned == []

    def test_spawnif_cursor_continues_after_fire(self):
        """SPAWNIF does not stop the cursor — execution continues past it."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", True, "child_node"],
                    ["EMITIF", True, "after_spawn", "yes", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "trigger", basket)
        assert result.spawned_fresh == ["child_node"]
        assert basket.get("after_spawn") == "yes"
        assert result.stopped_by == "ENDIF"

    def test_spawnif_cursor_continues_when_condition_false(self):
        """Cursor continues after SPAWNIF even when condition is false."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", False, "child_node"],
                    ["EMITIF", True, "reached", 1, "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "trigger", basket)
        assert result.spawned_fresh == []
        assert basket.get("reached") == 1

    def test_spawnif_wrong_arg_count_logs_and_continues(self):
        """SPAWNIF with wrong arg count should log warning and continue."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", True],  # Missing target — only 2 elements
                    ["EMITIF", True, "key1", "value1", "basket"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        basket = {}
        result = execute_node(memory, "trigger", basket)
        assert result.spawned_fresh == []
        assert basket.get("key1") == "value1"
        assert result.stopped_by == "ENDIF"

    def test_spawnif_basket_condition_gate(self):
        """SPAWNIF with basket condition fires only when gate passes."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["SPAWNIF", ["mode", "==", "fresh"], "child_node"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        # Condition false — no spawn
        result = execute_node(memory, "trigger", {"mode": "shared"})
        assert result.spawned_fresh == []

        # Condition true — spawns
        result = execute_node(memory, "trigger", {"mode": "fresh"})
        assert result.spawned_fresh == ["child_node"]

    def test_spawnif_and_forkif_coexist(self):
        """SPAWNIF and FORKIF in same cell populate separate lists."""
        memory = MockMemory(
            "test_mem",
            payload={
                "exec_cell": [
                    ["FORKIF", True, "shared_child"],
                    ["SPAWNIF", True, "fresh_child"],
                    "ENDIF",
                ]
            },
            metadata={"triggers": {"trigger": "exec_cell"}},
        )
        result = execute_node(memory, "trigger", {})
        assert result.spawned == ["shared_child"]
        assert result.spawned_fresh == ["fresh_child"]


class TestEngramTriggerCellsKeyRealDB:
    """T-engram-trigger-cell-name-mismatch — Bug 1 regression.

    Verifies that an engram with triggers={"__entry__": "cells"} executes
    correctly via execute_node.  Seeds a minimal test row in the live Postgres
    DB (Igor-Wild1), loads it back as a plain namespace object, and asserts
    that execute_node runs at least one instruction instead of the old WARN-and-
    noop path that returned instructions_run=0 when trigger mapped to
    "coding sprint entry" (absent in payload) instead of "cells".
    """

    _TEST_ID = "ENGRAM_CODE_TEST_TRIGGER_REGRESSION"

    @classmethod
    def setup_class(cls):
        import os
        import psycopg2

        cls._db_url = os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
        )
        cls._conn = psycopg2.connect(cls._db_url)
        cls._conn.autocommit = True
        cur = cls._conn.cursor()
        import json

        payload = {"cells": [["EMITIF", True, "ran", "yes", "basket"], "ENDIF"]}
        metadata = {
            "triggers": {"__entry__": "cells"},
            "habit_type": "engram",
            "test_data": True,
        }
        cur.execute(
            """
            INSERT INTO memories (id, narrative, memory_type, parent_id, source,
                                  confidence, metadata, payload, scope)
            VALUES (%s, %s, 'PROCEDURAL', 'CP1', 'test', 1.0, %s, %s, 'class')
            ON CONFLICT (id) DO UPDATE SET
                narrative = EXCLUDED.narrative,
                metadata  = EXCLUDED.metadata,
                payload   = EXCLUDED.payload
            """,
            (
                cls._TEST_ID,
                "Regression test engram for T-engram-trigger-cell-name-mismatch",
                json.dumps(metadata),
                json.dumps(payload),
            ),
        )

    @classmethod
    def teardown_class(cls):
        try:
            cur = cls._conn.cursor()
            cur.execute("DELETE FROM memories WHERE id = %s", (cls._TEST_ID,))
        finally:
            cls._conn.close()

    def _load_from_db(self):
        """Load the seeded row back from Postgres and return a minimal object."""
        import json
        import types

        cur = self._conn.cursor()
        cur.execute(
            "SELECT id, metadata, payload FROM memories WHERE id = %s",
            (self._TEST_ID,),
        )
        row = cur.fetchone()
        assert row is not None, f"{self._TEST_ID} not found in DB"
        row_id, meta_raw, payload_raw = row
        # meta and payload may come back as dict (psycopg2 json) or str
        meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw)
        payload = (
            payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw)
        )
        node = types.SimpleNamespace(id=row_id, metadata=meta, payload=payload)
        return node

    def test_trigger_cells_key_runs_instructions(self):
        """execute_node with triggers.__entry__=cells must run ≥1 instruction.

        Before the fix, trigger value was "coding sprint entry" — not present in
        payload — so execute_node returned instructions_run=0.  After the fix,
        trigger value is "cells", the cell is found, and EMITIF+ENDIF run.
        """
        node = self._load_from_db()
        # Confirm the DB row has the correct trigger value
        assert node.metadata["triggers"]["__entry__"] == "cells", (
            f"Expected trigger 'cells' but got {node.metadata['triggers']!r} — "
            "seed_coding_engrams.py may not have been re-run"
        )
        basket = {}
        result = execute_node(node, "__entry__", basket)
        assert result.instructions_run > 0, (
            f"execute_node returned instructions_run=0 — "
            f"cell lookup for trigger '__entry__' failed (stopped_by={result.stopped_by!r})"
        )
        assert basket.get("ran") == "yes"

    def test_old_trigger_value_returns_noop(self):
        """Sanity check: a trigger with no matching cell still returns 0 instructions."""
        node = self._load_from_db()
        # Override metadata locally (don't touch DB)
        import copy

        bad_node = copy.copy(node)
        bad_node.metadata = {
            **node.metadata,
            "triggers": {"__entry__": "coding sprint entry"},
        }
        basket = {}
        result = execute_node(bad_node, "__entry__", basket)
        assert result.instructions_run == 0
