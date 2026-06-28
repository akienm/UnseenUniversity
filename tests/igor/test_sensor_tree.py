"""
test_sensor_tree.py — GH-281: SensorTree

Tests for generalized monitoring as traversable memory subtree.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_cortex(sensors=None):
    """Create mock cortex with sensor tree support."""
    cortex = MagicMock()
    cortex.twm_push.return_value = 1
    cortex.store.return_value = None

    if sensors is not None:
        root = MagicMock()
        root.id = "SENSOR_TREE_ROOT"
        cortex.get.return_value = root

        children = []
        for s in sensors:
            child = MagicMock()
            child.id = s["id"]
            child.narrative = s.get("narrative", "")
            child.metadata = s.get("metadata", {})
            children.append(child)
        cortex.get_children.return_value = children
    else:
        cortex.get.return_value = None
        cortex.get_children.return_value = []

    return cortex


class TestEvaluators:
    def test_file_mtime_detects_change(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_file_mtime

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            result = _eval_file_mtime(path, {"_last_mtime": 0})
            assert result["triggered"] is True
            assert "mtime changed" in result["detail"]
        finally:
            os.unlink(path)

    def test_file_mtime_no_change(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_file_mtime

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            mtime = os.stat(path).st_mtime
            result = _eval_file_mtime(path, {"_last_mtime": mtime})
            assert result["triggered"] is False
        finally:
            os.unlink(path)

    def test_file_mtime_first_check(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_file_mtime

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            result = _eval_file_mtime(path, {})
            assert result["triggered"] is False
            assert "current_mtime" in result
        finally:
            os.unlink(path)

    def test_file_mtime_missing_file(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_file_mtime

        result = _eval_file_mtime("/nonexistent/path/xyz", {})
        assert result["triggered"] is True
        assert "not found" in result["detail"]

    def test_disk_usage_below_threshold(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_disk_usage

        result = _eval_disk_usage("/", {"threshold": 99.9})
        assert result["triggered"] is False

    def test_disk_usage_above_threshold(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_disk_usage

        result = _eval_disk_usage("/", {"threshold": 0.001})
        assert result["triggered"] is True

    def test_process_alive_check(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_process_alive

        # python should be running (us)
        result = _eval_process_alive("python", {"condition": "unreachable"})
        assert result["triggered"] is False
        assert "alive" in result["detail"]

    def test_process_not_found(self):
        from unseen_university.devices.igor.cognition.sensor_tree import _eval_process_alive

        result = _eval_process_alive(
            "nonexistent_process_xyz_12345", {"condition": "unreachable"}
        )
        assert result["triggered"] is True


class TestEvaluateSensor:
    def test_unknown_watch_type(self):
        from unseen_university.devices.igor.cognition.sensor_tree import evaluate_sensor

        result = evaluate_sensor({"watch_type": "quantum_flux", "target": "x"})
        assert result is None

    def test_missing_target(self):
        from unseen_university.devices.igor.cognition.sensor_tree import evaluate_sensor

        result = evaluate_sensor({"watch_type": "file_mtime"})
        assert result is None

    def test_valid_sensor(self):
        from unseen_university.devices.igor.cognition.sensor_tree import evaluate_sensor

        result = evaluate_sensor(
            {"watch_type": "disk_usage", "target": "/", "threshold": 99.9}
        )
        assert result is not None
        assert "triggered" in result


class TestSensorTreeSource:
    def test_no_sensors_no_push(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        source = SensorTreeSource()
        cortex = _make_cortex(sensors=[])
        pushed = source.push(cortex)
        assert pushed == []

    def test_no_root_no_push(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        source = SensorTreeSource()
        cortex = _make_cortex()  # no sensors, get returns None
        pushed = source.push(cortex)
        assert pushed == []

    def test_triggered_sensor_pushes_to_twm(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        sensors = [
            {
                "id": "SENSOR_DISK_ROOT",
                "narrative": "Monitor root disk",
                "metadata": {
                    "watch_type": "disk_usage",
                    "target": "/",
                    "threshold": 0.001,  # will trigger
                    "check_interval_sec": 0,
                },
            }
        ]
        source = SensorTreeSource()
        cortex = _make_cortex(sensors=sensors)
        pushed = source.push(cortex)
        assert len(pushed) >= 1
        cortex.twm_push.assert_called()
        call_kwargs = cortex.twm_push.call_args_list[0][1]
        assert "SENSOR_ALERT" in call_kwargs["content_csb"]
        assert call_kwargs["source"] == "sensor_tree"

    def test_suppresses_repeated_triggers(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        sensors = [
            {
                "id": "SENSOR_DISK_ROOT",
                "narrative": "Monitor root disk",
                "metadata": {
                    "watch_type": "disk_usage",
                    "target": "/",
                    "threshold": 0.001,
                    "check_interval_sec": 0,
                },
            }
        ]
        source = SensorTreeSource()
        cortex = _make_cortex(sensors=sensors)

        # First push triggers
        pushed1 = source.push(cortex)
        assert len(pushed1) >= 1

        # Second push is suppressed
        source._last_check = None  # reset interval check
        pushed2 = source.push(cortex)
        assert len(pushed2) == 0

    def test_non_triggered_clears_suppression(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        sensors = [
            {
                "id": "SENSOR_DISK_ROOT",
                "narrative": "Monitor root disk",
                "metadata": {
                    "watch_type": "disk_usage",
                    "target": "/",
                    "threshold": 99.9,  # won't trigger
                    "check_interval_sec": 0,
                },
            }
        ]
        source = SensorTreeSource()
        source._suppressed.add("SENSOR_DISK_ROOT")  # was previously suppressed

        cortex = _make_cortex(sensors=sensors)
        source.push(cortex)

        # Suppression should be cleared
        assert "SENSOR_DISK_ROOT" not in source._suppressed

    def test_action_habit_fires(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        sensors = [
            {
                "id": "SENSOR_WITH_ACTION",
                "narrative": "Sensor with action",
                "metadata": {
                    "watch_type": "disk_usage",
                    "target": "/",
                    "threshold": 0.001,
                    "action_habit_id": "HABIT_DISK_ALERT",
                    "check_interval_sec": 0,
                },
            }
        ]
        source = SensorTreeSource()
        cortex = _make_cortex(sensors=sensors)
        source.push(cortex)

        # Should have TWM pushes: one SENSOR_ALERT + one ACTION_IMPULSE
        assert cortex.twm_push.call_count >= 2
        action_calls = [
            c for c in cortex.twm_push.call_args_list if "ACTION_IMPULSE" in str(c)
        ]
        assert len(action_calls) == 1

    def test_respects_check_interval(self):
        from unseen_university.devices.igor.cognition.sensor_tree import SensorTreeSource

        import time

        source = SensorTreeSource()
        source._last_check = time.monotonic()  # just checked

        cortex = _make_cortex(sensors=[])
        pushed = source.push(cortex)
        assert pushed == []


class TestCreateSensor:
    def test_creates_sensor_node(self):
        from unseen_university.devices.igor.cognition.sensor_tree import create_sensor

        cortex = _make_cortex()
        # Pretend root exists
        cortex.get.return_value = MagicMock()

        result = create_sensor(
            cortex,
            sensor_id="SENSOR_TEST",
            narrative="Test sensor",
            watch_type="file_mtime",
            target="/tmp/test.txt",
        )
        assert result["sensor_id"] == "SENSOR_TEST"
        assert result["watch_type"] == "file_mtime"
        cortex.store.assert_called()
        stored = cortex.store.call_args[0][0]
        assert stored.id == "SENSOR_TEST"
        assert stored.metadata["watch_type"] == "file_mtime"
        assert stored.parent_id == "SENSOR_TREE_ROOT"
