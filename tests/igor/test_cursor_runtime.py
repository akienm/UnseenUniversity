"""
test_cursor_runtime.py — T-engram-cursor-runtime

Tests for the extracted cursor traversal runtime.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.cursor_runtime import CursorResult, run_cursor
from wild_igor.igor.cognition.node_executor import ExecutionResult


def _mock_cortex():
    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.get = MagicMock(return_value=None)
    return cortex


def _mock_node(node_id="TEST_NODE", payload=None, triggers=None):
    node = MagicMock()
    node.id = node_id
    node.payload = payload or {}
    node.metadata = {"triggers": triggers or {}, "habit_type": "engram"}
    return node


class TestRunCursorBasic:
    def test_single_node_no_branch(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("ENTRY")

        with patch("wild_igor.igor.cognition.cursor_runtime.execute_node") as mock_exec:
            mock_exec.return_value = ExecutionResult(stopped_by="implicit_end")
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.nodes_visited == 1
        assert result.stopped_by == "end"
        assert result.trace == ["ENTRY"]

    def test_two_node_chain(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node1 = _mock_node("NODE_A")
        node2 = _mock_node("NODE_B")
        cortex.get.return_value = node2

        call_count = [0]

        def fake_exec(node, trigger, basket, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ExecutionResult(next_node="NODE_B")
            return ExecutionResult(stopped_by="implicit_end")

        with patch(
            "wild_igor.igor.cognition.cursor_runtime.execute_node",
            side_effect=fake_exec,
        ):
            result = run_cursor(cortex, node1, "__entry__", {})

        assert result.nodes_visited == 2
        assert result.trace == ["NODE_A", "NODE_B"]
        assert result.stopped_by == "end"

    def test_missing_next_node(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        cortex.get.return_value = None  # next node doesn't exist
        node = _mock_node("ENTRY")

        with patch("wild_igor.igor.cognition.cursor_runtime.execute_node") as mock_exec:
            mock_exec.return_value = ExecutionResult(next_node="GHOST")
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.stopped_by == "missing_node"


class TestLoopDetection:
    def test_detects_loop(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("LOOPER")
        cortex.get.return_value = node  # always returns same node

        with patch("wild_igor.igor.cognition.cursor_runtime.execute_node") as mock_exec:
            mock_exec.return_value = ExecutionResult(next_node="LOOPER")
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.stopped_by == "loop"
        assert result.nodes_visited >= 2

    def test_max_steps_safety(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("STEPPER")

        call_count = [0]

        def fake_exec(n, t, b, **kwargs):
            call_count[0] += 1
            # Always branch to a "new" node (different basket each time)
            b[f"step_{call_count[0]}"] = True
            new_node = _mock_node(f"STEP_{call_count[0]}")
            cortex.get.return_value = new_node
            return ExecutionResult(next_node=f"STEP_{call_count[0]}")

        with patch(
            "wild_igor.igor.cognition.cursor_runtime.execute_node",
            side_effect=fake_exec,
        ):
            result = run_cursor(cortex, node, "__entry__", {}, max_steps=5)

        assert result.stopped_by == "max_steps"
        assert result.nodes_visited == 5


class TestSpawnedTargets:
    def test_accumulates_forkif(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("FORKER")

        with patch("wild_igor.igor.cognition.cursor_runtime.execute_node") as mock_exec:
            mock_exec.return_value = ExecutionResult(
                spawned=["FORK_A", "FORK_B"],
                stopped_by="implicit_end",
            )
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.spawned_fork == ["FORK_A", "FORK_B"]

    def test_accumulates_spawnif(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("SPAWNER")

        with patch("wild_igor.igor.cognition.cursor_runtime.execute_node") as mock_exec:
            mock_exec.return_value = ExecutionResult(
                spawned_fresh=["SPAWN_X"],
                stopped_by="implicit_end",
            )
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.spawned_fresh == ["SPAWN_X"]


class TestErrorHandling:
    def test_execution_error_stops_gracefully(self):
        from unittest.mock import patch

        cortex = _mock_cortex()
        node = _mock_node("CRASHER")

        with patch(
            "wild_igor.igor.cognition.cursor_runtime.execute_node",
            side_effect=RuntimeError("boom"),
        ):
            result = run_cursor(cortex, node, "__entry__", {})

        assert result.stopped_by == "error"
        assert "boom" in result.error
        # Ring should have error entry
        cortex.write_ring.assert_called()


class TestCursorResult:
    def test_defaults(self):
        r = CursorResult()
        assert r.nodes_visited == 0
        assert r.spawned_fork == []
        assert r.spawned_fresh == []
        assert r.stopped_by == "end"
        assert r.trace == []
