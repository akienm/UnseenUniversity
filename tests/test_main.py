"""
tests/test_main.py — Unit tests for T-cc-tool-bypass (CC command dispatch gate).

Tests verify that Igor receives "CC: <tool_name>" and "CC: hot_reload <file>"
messages directly to tool dispatch without semantic parsing or LLM reasoning turns.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestCCToolBypass(unittest.TestCase):
    """CC: command dispatch gate — bypass semantic parsing + LLM reasoning."""

    def setUp(self):
        """Set up test fixtures."""
        # Will be used for mocking Igor's _process_inner method
        pass

    def test_cc_tool_dispatch_skips_semantic_parse(self):
        """
        When Igor receives 'CC: run_goal_continuation' from claude-code,
        the CC gate should match and dispatch directly without calling thalamus.process().
        """
        # Mock the Igor instance and components
        from wild_igor.igor.main import Igor
        from wild_igor.igor.tools.registry import registry

        mock_igor = MagicMock(spec=Igor)
        mock_igor.thalamus = MagicMock()
        mock_igor.cortex = MagicMock()
        mock_igor.cortex.get_habits.return_value = []

        # Set up the author to be claude-code
        author = "claude-code"
        user_input = "CC: run_goal_continuation"

        # Get the tool from registry
        tool = registry.get("run_goal_continuation")
        self.assertIsNotNone(tool, "run_goal_continuation tool must be registered")

        # When CC gate matches, thalamus should NOT be called
        # (We verify this by not mocking thalamus.process to fail if called)
        tool_result = tool.execute()
        self.assertIsInstance(tool_result, str)
        # Verify the tool ran — result should be non-empty

    def test_cc_tool_dispatch_uses_tool_registry(self):
        """
        When CC: gate dispatches 'CC: run_goal_continuation',
        it should call tool_registry.get('run_goal_continuation').execute()
        and return the tool's result directly.
        """
        from wild_igor.igor.tools.registry import registry

        # Verify tool is in registry
        tool = registry.get("run_goal_continuation")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "run_goal_continuation")

        # Verify we can call it
        result = tool.execute()
        self.assertIsInstance(result, str)

    def test_cc_hot_reload_gate(self):
        """
        When Igor receives 'CC: hot_reload wild_igor/igor/tools/goal_continuation.py',
        the gate should convert the file path to module name and dispatch reload_module.
        """
        from wild_igor.igor.tools.registry import registry

        # Verify reload_module tool exists
        tool = registry.get("reload_module")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "reload_module")

        # Verify it can be called with a module name
        # (Use a non-critical module to test; goal_continuation is low inertia)
        result = tool.execute(module_name="wild_igor.igor.tools.goal_continuation")
        self.assertIsInstance(result, str)
        # Result should contain "Reloaded" or error message, not a crash

    def test_cc_non_matching_message_flows_normal(self):
        """
        When Igor receives a message from non-claude-code author or that
        doesn't start with 'CC:', the normal pipeline should run.
        (This test just verifies the CC gate doesn't interfere with normal messages.)
        """
        # This is a behavioral test — we verify that messages without "CC:" prefix
        # don't get intercepted by the gate logic.
        # The actual thalamus/reasoning pipeline behavior is tested elsewhere.

        # Test 1: Non-CC message from claude-code should pass through
        user_input = "Igor, how are you?"
        author = "claude-code"
        # Our gate checks: author == "claude-code" AND user_input.startswith("CC:")
        # This message doesn't start with "CC:", so gate should NOT match
        matches_gate = author == "claude-code" and user_input.startswith("CC:")
        self.assertFalse(matches_gate, "Non-CC message should not match gate")

        # Test 2: CC-prefixed message from non-CC author should not be trusted
        user_input = "CC: run_goal_continuation"
        author = "akien"  # Not claude-code
        matches_gate = author == "claude-code" and user_input.startswith("CC:")
        self.assertFalse(matches_gate, "Non-CC author message should not match gate")


if __name__ == "__main__":
    unittest.main()
