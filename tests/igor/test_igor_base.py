"""Tests for IgorBase flight-recorder logging methods."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from unseen_university.devices.igor.igor_base import IgorBase


class TestIgorBaseLLMIO:
    """Tests for log_llm_io() method."""

    def test_log_llm_io_writes_json_line(self):
        """Verify log_llm_io writes a parseable JSON line with all required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                logger.log_llm_io(
                    step="pe_plan",
                    prompt="This is a prompt",
                    response="This is a response",
                    model="claude-opus-4-6",
                    elapsed_ms=1500.0,
                )

                # Check that the log file was created
                log_file = (
                    mock_runtime
                    / "logs"
                    / "llm_io"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                assert log_file.exists()

                # Read and parse the JSON line
                with log_file.open() as f:
                    content = f.read().strip()
                    entry = json.loads(content)

                # Verify all required fields
                assert entry["step"] == "pe_plan"
                assert entry["model"] == "claude-opus-4-6"
                assert entry["elapsed_ms"] == 1500.0
                assert entry["prompt"] == "This is a prompt"
                assert entry["response"] == "This is a response"
                assert entry["prompt_len"] == 16
                assert entry["response_len"] == 18
                assert "ts" in entry

    def test_log_llm_io_caps_prompt_at_16kb(self):
        """Verify log_llm_io caps prompt at 16KB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                large_prompt = "x" * (20 * 1024)  # 20KB
                logger.log_llm_io(
                    step="test",
                    prompt=large_prompt,
                    response="response",
                    model="test",
                    elapsed_ms=100.0,
                )

                log_file = (
                    mock_runtime
                    / "logs"
                    / "llm_io"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                with log_file.open() as f:
                    entry = json.loads(f.read().strip())

                assert len(entry["prompt"]) == 16384
                assert entry["prompt_len"] == 20 * 1024  # Original length recorded

    def test_log_llm_io_caps_response_at_8kb(self):
        """Verify log_llm_io caps response at 8KB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                large_response = "y" * (10 * 1024)  # 10KB
                logger.log_llm_io(
                    step="test",
                    prompt="prompt",
                    response=large_response,
                    model="test",
                    elapsed_ms=100.0,
                )

                log_file = (
                    mock_runtime
                    / "logs"
                    / "llm_io"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                with log_file.open() as f:
                    entry = json.loads(f.read().strip())

                assert len(entry["response"]) == 8192
                assert entry["response_len"] == 10 * 1024  # Original length recorded

    def test_log_llm_io_exception_does_not_propagate(self):
        """Verify log_llm_io catches exceptions and never raises."""
        logger = IgorBase()

        with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
            mock_paths_fn.side_effect = RuntimeError("Forced error")
            # This should not raise even though paths() fails
            logger.log_llm_io(
                step="test",
                prompt="prompt",
                response="response",
                model="test",
                elapsed_ms=100.0,
            )
            # If we get here without an exception, the test passes


class TestIgorBaseStateSnapshot:
    """Tests for log_state_snapshot() method."""

    def test_log_state_snapshot_writes_json_line(self):
        """Verify log_state_snapshot writes a parseable JSON line with label and state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                test_state = {"key1": "value1", "key2": 42, "key3": [1, 2, 3]}
                logger.log_state_snapshot(label="test_label", state=test_state)

                # Check that the log file was created
                log_file = (
                    mock_runtime
                    / "logs"
                    / "snapshots"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                assert log_file.exists()

                # Read and parse the JSON line
                with log_file.open() as f:
                    content = f.read().strip()
                    entry = json.loads(content)

                # Verify required fields
                assert entry["label"] == "test_label"
                assert entry["state"] == test_state
                assert "ts" in entry

    def test_log_state_snapshot_with_complex_state(self):
        """Verify log_state_snapshot handles complex nested state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                complex_state = {
                    "nested": {"deep": {"value": "found"}},
                    "list": [1, 2, 3],
                    "mixed": {"array": [{"id": 1}, {"id": 2}]},
                }
                logger.log_state_snapshot(label="complex", state=complex_state)

                log_file = (
                    mock_runtime
                    / "logs"
                    / "snapshots"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                with log_file.open() as f:
                    entry = json.loads(f.read().strip())

                assert entry["state"] == complex_state

    def test_log_state_snapshot_exception_does_not_propagate(self):
        """Verify log_state_snapshot catches exceptions and never raises."""
        logger = IgorBase()

        with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
            mock_paths_fn.side_effect = RuntimeError("Forced error")
            # This should not raise even though paths() fails
            logger.log_state_snapshot(label="test", state={"key": "value"})
            # If we get here without an exception, the test passes

    def test_multiple_log_entries_in_same_file(self):
        """Verify multiple log_state_snapshot calls append to the same daily file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("unseen_university.devices.igor.igor_base.paths") as mock_paths_fn:
                mock_runtime = Path(tmpdir)
                mock_paths_fn.return_value.runtime = mock_runtime

                logger = IgorBase()
                logger.log_state_snapshot(label="first", state={"order": 1})
                logger.log_state_snapshot(label="second", state={"order": 2})

                log_file = (
                    mock_runtime
                    / "logs"
                    / "snapshots"
                    / f"{datetime.now().strftime('%Y%m%d')}.log"
                )
                with log_file.open() as f:
                    lines = f.read().strip().split("\n")

                assert len(lines) == 2
                entry1 = json.loads(lines[0])
                entry2 = json.loads(lines[1])
                assert entry1["label"] == "first"
                assert entry2["label"] == "second"
