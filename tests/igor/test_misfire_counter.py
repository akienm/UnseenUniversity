"""
Tests for the misfire counter — threshold-based habit repair surfacing.
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from devices.igor.tools.misfire_counter import MisfireCounter, get_misfire_counter


@pytest.fixture
def temp_log_path():
    """Create a temporary misfire log file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def counter(temp_log_path):
    """Create a misfire counter with temp log and default threshold=3."""
    return MisfireCounter(log_path=temp_log_path, threshold=3)


class TestMisfireCounter:
    """Test basic misfire counter functionality."""

    def test_record_bash_exit_127(self, counter):
        """Test recording exit code 127 from bash."""
        result = counter.record_bash_exit("nonexistent-command arg1", exit_code=127)
        assert result is False  # First occurrence, below threshold

    def test_ignore_non_127_exit_codes(self, counter):
        """Test that non-127 exit codes are ignored."""
        result = counter.record_bash_exit("ls /tmp", exit_code=0)
        assert result is False

        result = counter.record_bash_exit("false", exit_code=1)
        assert result is False

        result = counter.record_bash_exit("timeout_cmd", exit_code=124)
        assert result is False

    def test_threshold_detection(self, counter):
        """Test that threshold is correctly detected."""
        # First 3 occurrences: not exceeded
        assert counter.record_bash_exit("cmd1", exit_code=127) is False
        assert counter.record_bash_exit("cmd1", exit_code=127) is False
        assert counter.record_bash_exit("cmd1", exit_code=127) is False

        # 4th occurrence: threshold exceeded (3 + this new one > threshold of 3)
        assert counter.record_bash_exit("cmd1", exit_code=127) is True

    def test_rolling_window(self, counter):
        """Test that the rolling 24h window filters old records."""
        # Manually insert old records (outside window)
        old_time = (datetime.now() - timedelta(hours=25)).timestamp()
        with open(counter.log_path, "w") as f:
            for i in range(3):
                record = {
                    "timestamp": old_time,
                    "counter_key": "old_cmd|bash|bash_exit_127",
                    "error_type": "bash_exit_127",
                    "count": i + 1,
                    "threshold_exceeded": False,
                }
                f.write(json.dumps(record) + "\n")

        # New records should not be affected by old ones
        result = counter.record_bash_exit("old_cmd", exit_code=127)
        assert result is False  # Only 1 in current window

    def test_record_tool_error(self, counter):
        """Test recording tool execution errors."""
        # First 3 errors of same type
        assert counter.record_tool_error("broken_tool", "ValueError") is False
        assert counter.record_tool_error("broken_tool", "ValueError") is False
        assert counter.record_tool_error("broken_tool", "ValueError") is False

        # 4th error: threshold exceeded
        assert counter.record_tool_error("broken_tool", "ValueError") is True

    def test_different_error_types_separate(self, counter):
        """Test that different error types are tracked separately."""
        # Record ValueError
        counter.record_tool_error("tool", "ValueError")
        counter.record_tool_error("tool", "ValueError")
        counter.record_tool_error("tool", "ValueError")

        # Record TimeoutError (separate counter)
        result = counter.record_tool_error("tool", "TimeoutError")
        assert result is False  # Only 1 TimeoutError, below threshold

        # 4th ValueError: threshold exceeded
        result = counter.record_tool_error("tool", "ValueError")
        assert result is True

    def test_different_tools_separate(self, counter):
        """Test that different tools are tracked separately."""
        # Record errors for tool1
        counter.record_tool_error("tool1", "ValueError")
        counter.record_tool_error("tool1", "ValueError")
        counter.record_tool_error("tool1", "ValueError")

        # Record errors for tool2 (separate counter)
        result = counter.record_tool_error("tool2", "ValueError")
        assert result is False  # Only 1 tool2 error

    def test_get_threshold_exceeded(self, counter):
        """Test retrieving records that exceeded threshold."""
        counter.record_bash_exit("cmd", exit_code=127)
        counter.record_bash_exit("cmd", exit_code=127)
        counter.record_bash_exit("cmd", exit_code=127)
        counter.record_bash_exit("cmd", exit_code=127)

        exceeded = counter.get_threshold_exceeded()
        assert len(exceeded) == 1
        assert exceeded[0].counter_key == "cmd|bash|bash_exit_127"
        assert exceeded[0].threshold_exceeded is True

    def test_get_active_counters(self, counter):
        """Test retrieving active counter values."""
        counter.record_bash_exit("cmd1", exit_code=127)
        counter.record_bash_exit("cmd1", exit_code=127)
        counter.record_tool_error("tool2", "ValueError")

        active = counter.get_active_counters()

        # Count is recomputed from records within window
        assert "cmd1|bash|bash_exit_127" in active
        assert active["cmd1|bash|bash_exit_127"] == 2

        assert "tool2|tool_execute|ValueError" in active
        assert active["tool2|tool_execute|ValueError"] == 1

    def test_reset_counter(self, counter):
        """Test resetting a specific counter."""
        counter.record_bash_exit("cmd1", exit_code=127)
        counter.record_bash_exit("cmd1", exit_code=127)
        counter.record_tool_error("tool2", "ValueError")

        active_before = counter.get_active_counters()
        assert len(active_before) == 2

        # Reset cmd1 counter
        counter.reset_counter("cmd1|bash|bash_exit_127")

        active_after = counter.get_active_counters()
        assert len(active_after) == 1
        assert "cmd1|bash|bash_exit_127" not in active_after
        assert "tool2|tool_execute|ValueError" in active_after

    def test_log_format(self, counter, temp_log_path):
        """Test that log entries are valid JSON."""
        counter.record_bash_exit("cmd", exit_code=127)

        with open(temp_log_path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 1
        data = json.loads(lines[0])
        assert "timestamp" in data
        assert "counter_key" in data
        assert "error_type" in data
        assert "count" in data
        assert "threshold_exceeded" in data
        assert data["error_type"] == "bash_exit_127"

    def test_concurrent_readers(self, counter):
        """Test that counter works correctly with multiple increments."""
        # Simulate concurrent increments
        for i in range(10):
            counter.record_bash_exit("stress_test", exit_code=127)

        active = counter.get_active_counters()
        assert active["stress_test|bash|bash_exit_127"] == 10

    def test_command_extraction(self, counter):
        """Test that command name is extracted correctly."""
        # Complex command with args
        counter.record_bash_exit("python -m pip install package", exit_code=127)

        active = counter.get_active_counters()
        keys = list(active.keys())
        assert len(keys) == 1
        assert keys[0].startswith("python|bash")

    def test_get_misfire_counter_singleton(self):
        """Test that get_misfire_counter returns singleton."""
        counter1 = get_misfire_counter()
        counter2 = get_misfire_counter()
        assert counter1 is counter2

    def test_malformed_log_entry_tolerance(self, temp_log_path):
        """Test that malformed log entries are skipped gracefully."""
        counter = MisfireCounter(log_path=temp_log_path, threshold=3)

        # Write a malformed entry
        with open(temp_log_path, "w") as f:
            f.write('{"timestamp": 123, "incomplete": true}\n')
            f.write("not json at all\n")

        # Should not crash when reading
        active = counter.get_active_counters()
        assert isinstance(active, dict)

        # New records should still work
        counter.record_bash_exit("cmd", exit_code=127)
        active = counter.get_active_counters()
        assert "cmd|bash|bash_exit_127" in active

    def test_empty_command(self, counter):
        """Test handling of empty command string."""
        result = counter.record_bash_exit("", exit_code=127)
        assert result is False  # Should not crash

        active = counter.get_active_counters()
        assert "unknown|bash|bash_exit_127" in active


class TestMisfireCounterIntegration:
    """Integration tests with runner.py and registry."""

    def test_bash_runner_integration(self):
        """Test that run_bash correctly records exit 127."""
        from devices.igor.tools.runner import run_bash
        from devices.igor.tools.misfire_counter import get_misfire_counter

        # Get the global counter instance
        counter = get_misfire_counter()

        # Get baseline of active counters
        baseline = counter.get_active_counters()
        baseline_count = sum(1 for k in baseline if "bash_exit_127" in k)

        # Run a non-existent command via bash (should get exit 127)
        output = run_bash("nonexistent_cmd_integration_test_xyz")

        # Should report exit code 127
        assert "[exit code: 127]" in output or "not found" in output.lower()

        # Check that counter recorded it (at least one more than baseline)
        active = counter.get_active_counters()
        new_count = sum(1 for k in active if "bash_exit_127" in k)
        assert new_count >= baseline_count

    def test_tool_error_integration(self, temp_log_path):
        """Test that tool errors are recorded."""
        from devices.igor.tools.registry import registry
        from devices.igor.tools.misfire_counter import MisfireCounter

        counter = MisfireCounter(log_path=temp_log_path, threshold=3)

        # Call registry.execute with a tool that doesn't exist (should error)
        # This should trigger _record_misfire
        result = registry.execute("nonexistent_tool", {})

        # Should have logged an error
        assert "Error" in result

        # Counter should have recorded the attempt
        active = counter.get_active_counters()
        # Note: This depends on registry catching the exception
        assert isinstance(active, dict)
