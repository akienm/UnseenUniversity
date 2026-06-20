"""Tests for devlab.claudecode.stall_check — age calculation and stall detection."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from claudecode.stall_check import compute_stall_info, find_stalls, _parse_ts

_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
_THRESHOLD = 2.0


def _ts(hours_ago: float) -> str:
    """Return ISO timestamp string for a time N hours before _NOW."""
    return (_NOW - timedelta(hours=hours_ago)).isoformat()


class TestParseTs:
    def test_returns_none_on_none(self):
        assert _parse_ts(None) is None

    def test_parses_utc_offset(self):
        dt = _parse_ts("2026-06-06T20:55:31.737802+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parses_naive_as_utc(self):
        dt = _parse_ts("2026-06-06T20:55:31.737802")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_returns_none_on_garbage(self):
        assert _parse_ts("not-a-date") is None


class TestComputeStallInfo:
    def test_returns_none_when_not_in_progress(self):
        assert compute_stall_info({"status": "sprint"}, _NOW) is None
        assert compute_stall_info({"status": "hold"}, _NOW) is None
        assert compute_stall_info({"status": "closed"}, _NOW) is None

    def test_returns_none_within_threshold(self):
        t = {"status": "in_progress", "id": "T-foo", "dispatched_at": _ts(1.0)}
        assert compute_stall_info(t, _NOW, threshold_hours=_THRESHOLD) is None

    def test_returns_none_at_exactly_threshold(self):
        t = {"status": "in_progress", "id": "T-foo", "dispatched_at": _ts(2.0)}
        assert compute_stall_info(t, _NOW, threshold_hours=_THRESHOLD) is None

    def test_returns_info_beyond_threshold(self):
        t = {"status": "in_progress", "id": "T-foo", "title": "do the thing", "worker": "claude",
             "dispatched_at": _ts(3.0)}
        info = compute_stall_info(t, _NOW, threshold_hours=_THRESHOLD)
        assert info is not None
        assert info["id"] == "T-foo"
        assert 2.9 < info["age_hours"] < 3.1
        assert info["title"] == "do the thing"

    def test_falls_back_to_updated_at_when_no_dispatched_at(self):
        t = {"status": "in_progress", "id": "T-bar", "title": "bar", "worker": "claude",
             "updated_at": _ts(5.0)}
        info = compute_stall_info(t, _NOW, threshold_hours=_THRESHOLD)
        assert info is not None
        assert 4.9 < info["age_hours"] < 5.1

    def test_dispatched_at_wins_over_updated_at(self):
        t = {"status": "in_progress", "id": "T-baz", "title": "baz", "worker": "claude",
             "dispatched_at": _ts(3.0),
             "updated_at": _ts(10.0)}
        info = compute_stall_info(t, _NOW, threshold_hours=_THRESHOLD)
        assert info is not None
        assert 2.9 < info["age_hours"] < 3.1

    def test_returns_none_with_no_timing_info(self):
        t = {"status": "in_progress", "id": "T-baz"}
        assert compute_stall_info(t, _NOW) is None

    def test_strips_status_prefix_from_title(self):
        t = {"status": "in_progress", "id": "T-x", "title": "[in_progress] real title",
             "dispatched_at": _ts(3.0)}
        info = compute_stall_info(t, _NOW)
        assert info["title"] == "real title"


class TestFindStalls:
    def test_returns_empty_when_no_stalls(self):
        tasks = [
            {"status": "in_progress", "id": "T-a", "dispatched_at": _ts(1.0)},
            {"status": "sprint", "id": "T-b"},
        ]
        assert find_stalls(tasks, _NOW, threshold_hours=_THRESHOLD) == []

    def test_returns_stalls_sorted_oldest_first(self):
        tasks = [
            {"status": "in_progress", "id": "T-a", "title": "a", "worker": "c",
             "dispatched_at": _ts(3.0)},
            {"status": "in_progress", "id": "T-b", "title": "b", "worker": "c",
             "dispatched_at": _ts(6.0)},
            {"status": "in_progress", "id": "T-c", "title": "c", "worker": "c",
             "dispatched_at": _ts(1.0)},  # not stalled
        ]
        stalls = find_stalls(tasks, _NOW, threshold_hours=_THRESHOLD)
        assert len(stalls) == 2
        assert stalls[0]["id"] == "T-b"  # oldest first
        assert stalls[1]["id"] == "T-a"

    def test_custom_threshold(self):
        tasks = [
            {"status": "in_progress", "id": "T-a", "title": "a", "worker": "c",
             "dispatched_at": _ts(3.0)},
        ]
        assert find_stalls(tasks, _NOW, threshold_hours=5.0) == []
        assert len(find_stalls(tasks, _NOW, threshold_hours=2.0)) == 1
