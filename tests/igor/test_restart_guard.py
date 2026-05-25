"""T-daemon-supervisor-backoff-and-one-shot-audit — restart_guard tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from devices.igor.restart_guard import (
    HALT_FLAG_FILENAME,
    HISTORY_FILENAME,
    clear_history,
    halt_present,
    record_and_check,
    write_halt,
)


def test_first_restart_never_halts(tmp_path):
    should_halt, count = record_and_check(tmp_path, max_restarts=5, window_secs=600)
    assert should_halt is False
    assert count == 1


def test_halts_after_max_exceeded(tmp_path):
    now = 1000.0
    for i in range(5):
        should_halt, count = record_and_check(
            tmp_path, now=now + i, max_restarts=5, window_secs=600
        )
        assert should_halt is False
    # 6th restart within the window → halt
    should_halt, count = record_and_check(
        tmp_path, now=now + 5, max_restarts=5, window_secs=600
    )
    assert should_halt is True
    assert count == 6


def test_window_aged_entries_drop_off(tmp_path):
    now = 1000.0
    # Record 6 old restarts outside the 600s window
    for i in range(6):
        record_and_check(tmp_path, now=now + i, max_restarts=5, window_secs=600)
    # Jump forward past the window — old entries should be pruned
    should_halt, count = record_and_check(
        tmp_path, now=now + 1000, max_restarts=5, window_secs=600
    )
    assert should_halt is False
    assert count == 1  # only the new entry


def test_history_file_is_persisted(tmp_path):
    record_and_check(tmp_path, now=100.0, max_restarts=5, window_secs=600)
    history_path = tmp_path / HISTORY_FILENAME
    assert history_path.exists()
    data = json.loads(history_path.read_text())
    assert data == [100.0]


def test_corrupt_history_resets(tmp_path):
    (tmp_path / HISTORY_FILENAME).write_text("{not json")
    should_halt, count = record_and_check(tmp_path, now=100.0)
    # Shouldn't raise; recovers to a fresh history
    assert should_halt is False
    assert count == 1


def test_non_list_history_resets(tmp_path):
    (tmp_path / HISTORY_FILENAME).write_text('{"wrong": "shape"}')
    should_halt, count = record_and_check(tmp_path, now=100.0)
    assert should_halt is False
    assert count == 1


def test_write_halt_creates_flag(tmp_path):
    write_halt(tmp_path, 7, 600)
    assert halt_present(tmp_path)
    content = (tmp_path / HALT_FLAG_FILENAME).read_text()
    assert "7 restarts" in content
    assert "600s" in content


def test_halt_present_default_false(tmp_path):
    assert halt_present(tmp_path) is False


def test_clear_history_removes_file(tmp_path):
    record_and_check(tmp_path, now=100.0)
    assert (tmp_path / HISTORY_FILENAME).exists()
    clear_history(tmp_path)
    assert not (tmp_path / HISTORY_FILENAME).exists()
