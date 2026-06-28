"""Tests for devices/scraps/debug_extractor.py — deterministic debug extraction."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, mock_open, patch

import pytest

from unseen_university.devices.scraps.debug_extractor import (
    classify_error,
    extract,
    extract_log_window,
    extract_state_snapshot,
    parse_stack_trace,
)

# ── classify_error ─────────────────────────────────────────────────────────────


def test_classify_error_pe_chain_old_string():
    assert (
        classify_error("ValueError: old_string not found in file")
        == "pe_chain_old_string"
    )


def test_classify_error_ne_stuck():
    assert classify_error("NE stuck after 30 cycles") == "ne_stuck"


def test_classify_error_test_failure():
    assert (
        classify_error("AssertionError: expected True but got False") == "test_failure"
    )


def test_classify_error_schema():
    assert (
        classify_error('ProgrammingError: column "foo" does not exist')
        == "schema_error"
    )


def test_classify_error_scope_guard():
    assert classify_error("scope_guard block: HIGH inertia file") == "scope_guard_block"


def test_classify_error_safe_mode():
    assert classify_error("safe_mode trip at cycle 30") == "safe_mode_trip"


def test_classify_error_unknown():
    assert classify_error("everything is fine, nothing to see here") == "unknown"


# ── parse_stack_trace ──────────────────────────────────────────────────────────


def test_parse_stack_trace_standard_format():
    text = textwrap.dedent("""
        Traceback (most recent call last):
          File "/home/user/foo.py", line 42, in run
            result = bar()
          File "/home/user/bar.py", line 7, in bar
            raise ValueError("oops")
        ValueError: oops
    """)
    frames = parse_stack_trace(text)
    assert len(frames) == 2
    assert frames[0] == {"file": "/home/user/foo.py", "line": 42, "function": "run"}
    assert frames[1] == {"file": "/home/user/bar.py", "line": 7, "function": "bar"}


def test_parse_stack_trace_empty():
    assert parse_stack_trace("no traceback here") == []


# ── extract_state_snapshot ─────────────────────────────────────────────────────


def test_state_snapshot_test_component():
    text = "FAILED tests/test_foo.py::test_bar\nAssertionError: expected 1 got 2"
    snap = extract_state_snapshot("test", text)
    assert snap.get("failed_test") == "tests/test_foo.py::test_bar"
    assert "1 got 2" in snap.get("assertion", "")


def test_state_snapshot_ne_component():
    text = "goal = T-some-ticket\ncycle_5\nNARRATIVE: planning next action"
    snap = extract_state_snapshot("ne", text)
    assert "T-some-ticket" in snap.get("goal", "")
    assert snap.get("cycle") == 5
    assert "planning" in snap.get("last_narrative", "")


def test_state_snapshot_schema_component():
    text = 'ERROR column "payloads" does not exist'
    snap = extract_state_snapshot("schema", text)
    assert snap.get("object_name") == "payloads"


def test_state_snapshot_empty_text():
    snap = extract_state_snapshot("pe_chain", "")
    assert snap == {}


# ── extract_log_window ─────────────────────────────────────────────────────────


def test_extract_log_window_returns_lines_in_window(tmp_path):
    log_content = "\n".join(
        [
            "2026-05-28T09:59:00 INFO before window",
            "2026-05-28T10:05:01 ERROR in window — event A",
            "2026-05-28T10:05:30 INFO in window — event B",
            "2026-05-28T10:11:00 INFO after window",
        ]
    )
    log_file = tmp_path / "errors.log"
    log_file.write_text(log_content)

    with patch(
        "unseen_university.devices.scraps.debug_extractor._LOG_DIR",
        tmp_path,
    ):
        lines = extract_log_window("general", "2026-05-28T10:05:00", window_minutes=5)

    assert len(lines) == 2
    assert "event A" in lines[0]
    assert "event B" in lines[1]


def test_extract_log_window_missing_log_returns_empty(tmp_path):
    with patch("unseen_university.devices.scraps.debug_extractor._LOG_DIR", tmp_path):
        lines = extract_log_window("general", "2026-05-28T10:00:00")
    assert lines == []


def test_extract_log_window_invalid_timestamp():
    lines = extract_log_window("general", "not-a-timestamp")
    assert lines == []


# ── extract (integration) ──────────────────────────────────────────────────────


def test_extract_with_text_input():
    text = textwrap.dedent("""
        2026-05-28T10:00:00 ERROR AssertionError: expected True but got False
          File "/home/user/test_foo.py", line 12, in test_bar
            assert result
    """).strip()

    result = extract({"component": "test", "text": text})

    assert result["error_type"] == "test_failure"
    assert len(result["stack_trace"]) == 1
    assert result["stack_trace"][0]["line"] == 12
    assert "AssertionError" in result["raw_error"]
    assert len(result["log_window"]) > 0


def test_extract_with_no_input_returns_empty():
    result = extract({})
    assert result["log_window"] == []
    assert result["error_type"] == "unknown"
    assert result["stack_trace"] == []
    assert result["raw_error"] == ""
    assert result["state_snapshot"] == {}
