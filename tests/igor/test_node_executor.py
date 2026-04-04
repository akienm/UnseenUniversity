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

from wild_igor.igor.cognition.node_executor import execute_node, ExecutionResult
from wild_igor.igor.memory.models import Memory, MemoryType


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
        from wild_igor.igor.tools.registry import Tool, ToolRegistry

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
            "wild_igor.igor.cognition.node_executor._tool_registry", mock_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert result.stopped_by == "ENDIF"
        assert basket["call_result"] == "echo:hello"

    def test_mcpcall_unknown_tool_stores_error(self):
        """MCPCALL with unknown tool name writes __error__ to basket; execution continues."""
        from unittest.mock import patch
        from wild_igor.igor.tools.registry import ToolRegistry

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
            "wild_igor.igor.cognition.node_executor._tool_registry", empty_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert "__error__" in basket["result"]
        assert basket.get("after") == "yes"  # execution continues past error

    def test_mcpcall_tool_exception_stores_error(self):
        """MCPCALL stores __error__ in basket when tool raises; execution continues."""
        from unittest.mock import patch
        from wild_igor.igor.tools.registry import Tool, ToolRegistry

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
            "wild_igor.igor.cognition.node_executor._tool_registry", mock_registry
        ):
            result = execute_node(memory, "my_trigger", basket)

        assert "tool exploded" in basket["out"]["__error__"]
        assert basket.get("continued") == "yes"

    def test_mcpcall_tool_name_from_basket(self):
        """MCPCALL resolves tool name from basket when given [\"basket\", key]."""
        from unittest.mock import patch
        from wild_igor.igor.tools.registry import Tool, ToolRegistry

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
            "wild_igor.igor.cognition.node_executor._tool_registry", mock_registry
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
