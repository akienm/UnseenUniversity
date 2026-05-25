"""Tests for lab/claudecode/skill_telemetry.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Allow importing from lab/claudecode without installing
sys.path.insert(0, str(Path(__file__).parent.parent))
from lab.claudecode.skill_telemetry import (
    append_outcome,
    append_violation,
    monthly_rollup,
    skill_outcome_trend,
    top_violations,
    violation_log_path,
    outcome_log_path,
)


@pytest.fixture()
def tmp_igor_home(tmp_path, monkeypatch):
    """Point IGOR_HOME to a temp directory so tests don't touch real logs."""
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    (tmp_path / "claudecode").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestAppendViolationRoundTrip:
    def test_writes_record_to_jsonl(self, tmp_igor_home):
        rec = append_violation("sprint", "always-test-before-commit", "skipped tests")
        log = violation_log_path()
        assert log.exists()
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["skill"] == "sprint"
        assert data["flag_name"] == "always-test-before-commit"
        assert data["context"] == "skipped tests"

    def test_appends_multiple_records(self, tmp_igor_home):
        append_violation("sprint", "flag-a", "ctx1")
        append_violation("day-close", "flag-b", "ctx2")
        log = violation_log_path()
        lines = log.read_text().splitlines()
        assert len(lines) == 2


class TestTopViolations:
    def test_empty_log_returns_empty(self, tmp_igor_home):
        result = top_violations()
        assert result == []

    def test_returns_sorted_by_count(self, tmp_igor_home):
        append_violation("sprint", "flag-x", "a")
        append_violation("sprint", "flag-x", "b")
        append_violation("sprint", "flag-y", "c")
        result = top_violations(n=5)
        assert result[0] == ("sprint", "flag-x", 2)
        assert result[1] == ("sprint", "flag-y", 1)

    def test_respects_n_limit(self, tmp_igor_home):
        for i in range(5):
            append_violation(f"skill-{i}", "flag", "ctx")
        result = top_violations(n=2)
        assert len(result) == 2


class TestGracefulEmptyLog:
    def test_top_violations_no_file(self, tmp_igor_home):
        assert top_violations() == []

    def test_skill_outcome_trend_no_file(self, tmp_igor_home):
        assert skill_outcome_trend("sprint") == {}

    def test_monthly_rollup_no_file(self, tmp_igor_home):
        assert monthly_rollup() == []


class TestSkillOutcomeTrend:
    def test_returns_per_metric_history(self, tmp_igor_home):
        append_outcome("sprint", {"tests-pass": True, "no-debug-prints": False})
        append_outcome("sprint", {"tests-pass": True})
        trend = skill_outcome_trend("sprint")
        assert trend["tests-pass"] == [True, True]
        assert trend["no-debug-prints"] == [False]

    def test_filters_by_skill(self, tmp_igor_home):
        append_outcome("sprint", {"tests-pass": True})
        append_outcome("day-close", {"audit-runs": True})
        trend = skill_outcome_trend("sprint")
        assert "audit-runs" not in trend
        assert "tests-pass" in trend
