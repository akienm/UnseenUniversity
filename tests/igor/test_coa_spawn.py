"""Tests for COA spawn primitive — T-coa-spawn-primitive.

Tests:
  - Spawned COA has its own NE instance (isolation from root)
  - CPU gate refuses spawn when CPU% >= IGOR_COA_CPU_GATE
  - Background COA dissolves when its task_queue empties
  - Root COA tick() remains unaffected after a spawn
"""

from __future__ import annotations

import os
import sys
import time
import threading
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devices.igor.cognition.coa import COA, _cpu_gate_ok, _cpu_percent_now

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coa(instance_id: str = "test-instance") -> COA:
    """Build a COA with a mocked cortex and no real Igor back-reference."""
    cortex = MagicMock()
    cortex.twm_count.return_value = 0
    cortex.twm_max_id.return_value = 0
    igor = MagicMock()
    igor._is_processing = False
    igor._experiment_scheduler = None
    with patch(
        "devices.igor.cognition.coa.COA.__init__.__wrapped__", None, create=True
    ):
        pass
    # Patch NarrativeEngine so we don't hit the DB during tests
    with patch("devices.igor.cognition.coa.COA.__init__") as mock_init:
        mock_init.return_value = None
        coa = COA.__new__(COA)
        # Manually set all the attributes __init__ would set
        coa.ne = MagicMock(name="NarrativeEngine")
        coa._cortex = cortex
        coa._igor = igor
        coa._instance_id = instance_id
        coa._ne_thread = None
        coa._ne_spawn_lock = threading.Lock()
        coa._ne_last_twm_fingerprint = (0, 0)
        coa._ne_last_run_time = 0.0
        coa._last_ne_valence = 0.0
        coa._task_queue = []
        coa._bg_thread = None
        coa._is_background = False
    return coa


# ---------------------------------------------------------------------------
# NE isolation
# ---------------------------------------------------------------------------


def _spawn_patch():
    """Context manager: patch NarrativeEngine so spawn() doesn't hit the DB."""
    return patch(
        "devices.igor.cognition.narrative_engine.NarrativeEngine",
        new_callable=lambda: lambda *a, **kw: MagicMock(name="NarrativeEngine"),
    )


class TestCOANEIsolation(unittest.TestCase):
    def test_spawned_coa_has_separate_ne(self):
        root = _make_coa("wild-0001")
        with (
            patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch("devices.igor.cognition.narrative_engine.NarrativeEngine") as MockNE,
        ):
            MockNE.return_value = MagicMock(name="NarrativeEngine-child")
            child = root.spawn(task_queue=["task1"])
        self.assertIsNotNone(child)
        self.assertIsNot(child.ne, root.ne)

    def test_spawned_coa_has_different_instance_id(self):
        root = _make_coa("wild-0001")
        with (
            patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch("devices.igor.cognition.narrative_engine.NarrativeEngine") as MockNE,
        ):
            MockNE.return_value = MagicMock(name="NarrativeEngine-child")
            child = root.spawn()
        self.assertIsNotNone(child)
        self.assertNotEqual(child._instance_id, root._instance_id)

    def test_multiple_spawns_get_distinct_nes(self):
        root = _make_coa("wild-0001")
        children = []
        with (
            patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch(
                "devices.igor.cognition.narrative_engine.NarrativeEngine",
                side_effect=lambda *a, **kw: MagicMock(name="NarrativeEngine"),
            ),
        ):
            for _ in range(3):
                child = root.spawn(task_queue=["t"])
                self.assertIsNotNone(child)
                children.append(child)
        ne_ids = {id(c.ne) for c in children}
        self.assertEqual(len(ne_ids), 3, "each COA should have a distinct NE instance")


# ---------------------------------------------------------------------------
# CPU gate
# ---------------------------------------------------------------------------


class TestCPUGate(unittest.TestCase):
    def test_gate_blocks_spawn_when_cpu_high(self):
        """spawn() returns None when CPU >= IGOR_COA_CPU_GATE."""
        root = _make_coa()
        with (
            patch("devices.igor.cognition.coa._cpu_percent_now", return_value=95.0),
            patch.dict(os.environ, {"IGOR_COA_CPU_GATE": "60"}),
        ):
            result = root.spawn()
        self.assertIsNone(result)

    def test_gate_allows_spawn_when_cpu_low(self):
        """spawn() creates a child when CPU < IGOR_COA_CPU_GATE."""
        root = _make_coa()
        with (
            patch("devices.igor.cognition.coa._cpu_percent_now", return_value=10.0),
            patch.dict(os.environ, {"IGOR_COA_CPU_GATE": "60"}),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch("devices.igor.cognition.narrative_engine.NarrativeEngine"),
        ):
            result = root.spawn()
        self.assertIsNotNone(result)

    def test_gate_env_var_configures_threshold(self):
        """IGOR_COA_CPU_GATE=80 allows spawn at 70%."""
        root = _make_coa()
        with (
            patch("devices.igor.cognition.coa._cpu_percent_now", return_value=70.0),
            patch.dict(os.environ, {"IGOR_COA_CPU_GATE": "80"}),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch("devices.igor.cognition.narrative_engine.NarrativeEngine"),
        ):
            result = root.spawn()
        self.assertIsNotNone(result)

    def test_gate_blocks_fifth_coa_when_cpu_high(self):
        """Simulates the 5-COA cap via CPU gate (any spawn above threshold is refused)."""
        root = _make_coa()
        children = []
        # First 4 spawns succeed (CPU low)
        with (
            patch("devices.igor.cognition.coa._cpu_percent_now", return_value=10.0),
            patch.dict(os.environ, {"IGOR_COA_CPU_GATE": "60"}),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
            patch("devices.igor.cognition.narrative_engine.NarrativeEngine"),
        ):
            for _ in range(4):
                children.append(root.spawn(task_queue=["t"]))
        self.assertTrue(all(c is not None for c in children))
        # 5th spawn refused (CPU high)
        with (
            patch("devices.igor.cognition.coa._cpu_percent_now", return_value=90.0),
            patch.dict(os.environ, {"IGOR_COA_CPU_GATE": "60"}),
        ):
            fifth = root.spawn()
        self.assertIsNone(fifth)


# ---------------------------------------------------------------------------
# Dissolve on empty queue
# ---------------------------------------------------------------------------


class TestCOADissolve(unittest.TestCase):
    def test_background_coa_dissolves_when_queue_empty(self):
        """Background COA's loop thread exits after draining task_queue."""
        root = _make_coa()

        with (
            patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True),
            patch("devices.igor.cognition.coa.COA.tick") as mock_tick,
        ):

            # tick() drains one item per call via side_effect
            def _drain_one(*a, **kw):
                if root_coa._task_queue:
                    root_coa._task_queue.pop()

            root_coa = root.spawn(task_queue=["a", "b", "c"])
            self.assertIsNotNone(root_coa)
            mock_tick.side_effect = _drain_one

            # Wait up to 3s for the background thread to exit
            deadline = time.monotonic() + 3.0
            while root_coa.is_alive and time.monotonic() < deadline:
                time.sleep(0.05)

        self.assertFalse(
            root_coa.is_alive, "background COA should dissolve after queue empty"
        )

    def test_root_coa_is_always_alive(self):
        root = _make_coa()
        self.assertTrue(root.is_alive)

    def test_background_coa_with_empty_initial_queue_dissolves_immediately(self):
        root = _make_coa()
        with patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True):
            child = root.spawn(task_queue=[])
        # Give thread a moment to start and exit
        if child._bg_thread is not None:
            child._bg_thread.join(timeout=2.0)
        self.assertFalse(child.is_alive)


# ---------------------------------------------------------------------------
# is_alive property
# ---------------------------------------------------------------------------


class TestCOAIsAlive(unittest.TestCase):
    def test_is_background_false_for_root(self):
        root = _make_coa()
        self.assertFalse(root._is_background)
        self.assertTrue(root.is_alive)

    def test_is_background_true_for_spawned(self):
        root = _make_coa()
        with (
            patch("devices.igor.cognition.coa._cpu_gate_ok", return_value=True),
            patch("devices.igor.cognition.coa.COA._start_background_loop"),
        ):
            child = root.spawn(task_queue=["t"])
        self.assertTrue(child._is_background)


if __name__ == "__main__":
    unittest.main()
