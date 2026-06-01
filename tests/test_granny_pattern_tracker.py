"""
Tests for PatternTracker — PA2.0 Layer 1→2 observe→discover.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.granny.pattern_tracker import PatternTracker


@pytest.fixture
def tracker(tmp_path):
    return PatternTracker(corpus_path=tmp_path / "patterns.jsonl", report_every=5)


# ── corpus writes ─────────────────────────────────────────────────────────────


def test_record_appends_to_corpus(tracker, tmp_path):
    tracker.record(
        "T-foo", ["Platform"], "worker", "S", "DONE", iterations=3, cost_usd=0.001
    )
    lines = (tmp_path / "patterns.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["ticket_id"] == "T-foo"
    assert entry["signal"] == "DONE"
    assert entry["task_class"] == "worker"
    assert entry["cost_usd"] == pytest.approx(0.001)


def test_record_multiple_appends(tracker, tmp_path):
    for i in range(3):
        tracker.record(f"T-{i}", ["Infra"], "worker", "S", "ESCALATE: worker")
    lines = (tmp_path / "patterns.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_record_includes_advisor_signal_when_present(tracker, tmp_path):
    tracker.record(
        "T-x", ["Platform"], "analyst", "M", "DONE", advisor_signal="CONTINUE"
    )
    entry = json.loads((tmp_path / "patterns.jsonl").read_text())
    assert entry["advisor_signal"] == "CONTINUE"


def test_record_omits_advisor_signal_when_absent(tracker, tmp_path):
    tracker.record("T-x", ["Platform"], "worker", "S", "DONE")
    entry = json.loads((tmp_path / "patterns.jsonl").read_text())
    assert "advisor_signal" not in entry


# ── pattern_summary ───────────────────────────────────────────────────────────


def test_pattern_summary_empty(tracker):
    summary = tracker.pattern_summary()
    assert summary["total_dispatches"] == 0
    assert summary["patterns"] == []


def test_pattern_summary_done_rate(tracker):
    tracker.record("T-1", ["Platform"], "worker", "S", "DONE")
    tracker.record("T-2", ["Platform"], "worker", "S", "DONE")
    tracker.record("T-3", ["Platform"], "worker", "S", "ESCALATE: worker")
    summary = tracker.pattern_summary()
    assert summary["total_dispatches"] == 3
    pat = summary["patterns"][0]
    assert pat["tag"] == "Platform"
    assert pat["done_pct"] == pytest.approx(66.7, abs=0.1)
    assert pat["escalate_pct"] == pytest.approx(33.3, abs=0.1)


def test_pattern_summary_sorted_by_escalation_rate(tracker):
    # High escalation tag
    for _ in range(4):
        tracker.record("T-a", ["HighEsc"], "worker", "S", "ESCALATE: worker")
    # Low escalation tag
    tracker.record("T-b", ["LowEsc"], "worker", "S", "DONE")
    tracker.record("T-c", ["LowEsc"], "worker", "S", "DONE")
    summary = tracker.pattern_summary()
    assert summary["patterns"][0]["tag"] == "HighEsc"


# ── should_report / format_report ────────────────────────────────────────────


def test_should_report_fires_at_interval(tracker):
    for i in range(4):
        tracker.record(f"T-{i}", ["Tag"], "worker", "S", "DONE")
        assert not tracker.should_report()
    tracker.record("T-4", ["Tag"], "worker", "S", "DONE")
    assert tracker.should_report()


def test_format_report_contains_pattern_report_header(tracker):
    tracker.record("T-1", ["Platform"], "worker", "S", "ESCALATE: worker")
    report = tracker.format_report()
    assert "PATTERN_REPORT" in report
    assert "Platform" in report


def test_format_report_no_crash_on_empty(tracker):
    report = tracker.format_report()
    assert "PATTERN_REPORT" in report


# ── GrannyDaemon wiring ───────────────────────────────────────────────────────


def test_daemon_record_inference_outcome_calls_tracker():
    from devices.granny.daemon import GrannyDaemon
    from devices.minion.shim import WorkerResult

    with (
        patch("devices.granny.daemon.IMAPServer"),
        patch("lab.claudecode.cc_task_listener.TaskListener"),
    ):
        daemon = GrannyDaemon.__new__(GrannyDaemon)
        mock_tracker = MagicMock()
        daemon._pattern_tracker = mock_tracker
        daemon._post_channel = MagicMock()

        result = WorkerResult(signal="DONE", notes="ok", iterations=3, cost_usd=0.002)
        ticket = {"id": "T-x", "tags": ["Platform"], "size": "S"}
        daemon._record_inference_outcome(result, "worker", ticket)

        mock_tracker.record.assert_called_once_with(
            ticket_id="T-x",
            tags=["Platform"],
            task_class="worker",
            size="S",
            signal="DONE",
            iterations=3,
            cost_usd=0.002,
            advisor_signal=None,
        )


def test_daemon_posts_channel_on_report_interval():
    from devices.granny.daemon import GrannyDaemon
    from devices.minion.shim import WorkerResult

    with (
        patch("devices.granny.daemon.IMAPServer"),
        patch("lab.claudecode.cc_task_listener.TaskListener"),
    ):
        daemon = GrannyDaemon.__new__(GrannyDaemon)
        mock_tracker = MagicMock()
        mock_tracker.should_report.return_value = True
        mock_tracker.format_report.return_value = "PATTERN_REPORT|total=50"
        daemon._pattern_tracker = mock_tracker
        daemon._post_channel = MagicMock()

        result = WorkerResult(signal="ESCALATE: worker", notes="stuck", iterations=20)
        daemon._record_inference_outcome(
            result, "worker", {"id": "T-y", "tags": [], "size": "M"}
        )

        daemon._post_channel.assert_called_once_with("PATTERN_REPORT|total=50")
