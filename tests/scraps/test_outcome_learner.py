"""Tests for inference_outcome_learner: aggregate correctness, empty-channel graceful,
OUTCOME_LEARNER_REPORT format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unseen_university.devices.scraps.jobs.inference_outcome_learner import (
    _report_channel_message,
    aggregate,
    format_report,
    outcome_summary,
    parse_dispatch,
    parse_minion_result,
    top_insights,
)

# ── Parse helpers ─────────────────────────────────────────────────────────────


def _result(
    ticket, signal, task_class, iterations=3, cost_usd=0.01, advisor_signal=None
):
    """Build a MINION_RESULT channel content string."""
    adv = f"|advisor_signal={advisor_signal}" if advisor_signal else ""
    return (
        f"MINION_RESULT|ticket={ticket}|signal={signal}|task_class={task_class}"
        f"|iterations={iterations}|rounds=1|advisor_calls=0{adv}"
        f"|cost_usd={cost_usd:.4f}|tokens_in=100|tokens_out=50"
    )


def _dispatch(ticket, tags, size, worker="worker"):
    return (
        f"GRANNY_DISPATCH|ticket={ticket}|worker={worker}|size={size}"
        f"|tags={','.join(tags)}|title=Test ticket"
    )


# ── Test 1: aggregate correctness ─────────────────────────────────────────────


class TestAggregateCorrectness:
    def test_done_pct_computed_correctly(self):
        results = [
            parse_minion_result(_result("T-1", "DONE", "worker")),
            parse_minion_result(_result("T-2", "DONE", "worker")),
            parse_minion_result(_result("T-3", "ESCALATE: worker", "worker")),
        ]
        dispatches = {
            "T-1": parse_dispatch(_dispatch("T-1", ["Platform"], "M")),
            "T-2": parse_dispatch(_dispatch("T-2", ["Platform"], "M")),
            "T-3": parse_dispatch(_dispatch("T-3", ["Platform"], "M")),
        }
        agg = aggregate(results, dispatches)
        assert len(agg) == 1
        row = agg[0]
        assert row["tag"] == "Platform"
        assert row["task_class"] == "worker"
        assert row["size"] == "M"
        assert row["total"] == 3
        assert row["done_pct"] == pytest.approx(66.7, abs=0.1)
        assert row["escalate_pct"] == pytest.approx(33.3, abs=0.1)

    def test_multi_tag_ticket_counted_in_each_tag_bucket(self):
        results = [parse_minion_result(_result("T-1", "DONE", "worker"))]
        dispatches = {
            "T-1": parse_dispatch(_dispatch("T-1", ["Platform", "Scraps"], "S")),
        }
        agg = aggregate(results, dispatches)
        tags_seen = {r["tag"] for r in agg}
        assert "Platform" in tags_seen
        assert "Scraps" in tags_seen
        for row in agg:
            assert row["total"] == 1
            assert row["done_pct"] == 100.0

    def test_avg_iterations_and_cost(self):
        results = [
            parse_minion_result(
                _result("T-1", "DONE", "minion", iterations=4, cost_usd=0.02)
            ),
            parse_minion_result(
                _result("T-2", "DONE", "minion", iterations=6, cost_usd=0.04)
            ),
        ]
        dispatches = {
            "T-1": parse_dispatch(_dispatch("T-1", ["Infra"], "S")),
            "T-2": parse_dispatch(_dispatch("T-2", ["Infra"], "S")),
        }
        agg = aggregate(results, dispatches)
        assert len(agg) == 1
        assert agg[0]["avg_iterations"] == pytest.approx(5.0)
        assert agg[0]["avg_cost_usd"] == pytest.approx(0.03, abs=0.0001)

    def test_escalate_variants_normalised_to_escalate(self):
        results = [
            parse_minion_result(_result("T-1", "ESCALATE: worker", "worker")),
            parse_minion_result(_result("T-2", "ESCALATE: analyst", "worker")),
            parse_minion_result(_result("T-3", "ESCALATE: designer", "worker")),
        ]
        dispatches = {
            k: parse_dispatch(_dispatch(k, ["X"], "M")) for k in ("T-1", "T-2", "T-3")
        }
        agg = aggregate(results, dispatches)
        assert agg[0]["escalate_pct"] == 100.0
        assert agg[0]["done_pct"] == 0.0

    def test_no_dispatch_bucketed_unknown(self):
        results = [parse_minion_result(_result("T-orphan", "DONE", "worker"))]
        agg = aggregate(results, {})
        assert len(agg) == 1
        assert agg[0]["tag"] == "unknown"
        assert agg[0]["size"] == "?"

    def test_advisor_signal_counted(self):
        results = [
            parse_minion_result(
                _result("T-1", "ESCALATE: worker", "worker", advisor_signal="CONFUSED")
            ),
            parse_minion_result(
                _result("T-2", "ESCALATE: worker", "worker", advisor_signal="BLOCKED")
            ),
            parse_minion_result(_result("T-3", "DONE", "worker")),
        ]
        dispatches = {
            k: parse_dispatch(_dispatch(k, ["App"], "L")) for k in ("T-1", "T-2", "T-3")
        }
        agg = aggregate(results, dispatches)
        assert len(agg) == 1
        row = agg[0]
        assert row["confused_pct"] == pytest.approx(33.3, abs=0.1)
        assert row["blocked_pct"] == pytest.approx(33.3, abs=0.1)


# ── Test 2: empty-channel graceful ────────────────────────────────────────────


class TestEmptyChannelGraceful:
    def test_aggregate_empty_returns_empty_list(self):
        assert aggregate([], {}) == []

    def test_top_insights_empty_returns_placeholder(self):
        insights = top_insights([])
        assert len(insights) >= 1
        assert isinstance(insights[0], str)

    def test_format_report_empty(self):
        report = format_report(
            [],
            ["No data"],
            {"since_id": None, "limit": 200, "result_count": 0, "row_count": 0},
        )
        assert "generated_at" in report
        assert report["aggregates"] == []
        assert len(report["insights"]) >= 1

    def test_outcome_summary_no_file(self, tmp_path):
        missing = tmp_path / "nonexistent_report.json"
        summary = outcome_summary(missing)
        assert "no report yet" in summary

    def test_outcome_summary_reads_persisted_report(self, tmp_path):
        report_path = tmp_path / "outcome_report.json"
        report = {
            "generated_at": "2026-06-01T00:00:00+00:00",
            "window": {"limit": 200, "result_count": 5},
            "aggregates": [],
            "insights": ["Platform/M/worker: 80% escalate (5 runs) — try analyst tier"],
        }
        report_path.write_text(json.dumps(report))
        summary = outcome_summary(report_path)
        assert "5 results" in summary
        assert "Platform" in summary


# ── Test 3: OUTCOME_LEARNER_REPORT format ─────────────────────────────────────


class TestOutcomeLearnerReportFormat:
    def test_channel_message_starts_with_keyword(self):
        report = format_report(
            [],
            ["some insight"],
            {"limit": 200, "result_count": 10, "since_id": None, "row_count": 10},
        )
        msg = _report_channel_message(report)
        assert msg.startswith("OUTCOME_LEARNER_REPORT|")

    def test_channel_message_contains_window_and_results(self):
        report = format_report(
            [],
            ["insight one", "insight two"],
            {"limit": 200, "result_count": 7, "since_id": None, "row_count": 8},
        )
        msg = _report_channel_message(report)
        assert "window=200" in msg
        assert "results=7" in msg

    def test_channel_message_embeds_insights(self):
        report = format_report(
            [],
            ["Platform/M/worker: 80% escalate (5 runs) — try analyst tier"],
            {"limit": 200, "result_count": 5, "since_id": None, "row_count": 5},
        )
        msg = _report_channel_message(report)
        assert "insight_1=" in msg
        assert "Platform" in msg

    def test_report_json_has_required_keys(self):
        report = format_report(
            aggregate(
                [parse_minion_result(_result("T-1", "DONE", "worker"))],
                {"T-1": parse_dispatch(_dispatch("T-1", ["Platform"], "M"))},
            ),
            top_insights([]),
            {"limit": 200, "result_count": 1, "since_id": None, "row_count": 2},
        )
        for key in ("generated_at", "window", "aggregates", "insights"):
            assert key in report
        assert isinstance(report["aggregates"], list)
        assert isinstance(report["insights"], list)
        agg_row = report["aggregates"][0]
        for field in (
            "tag",
            "task_class",
            "size",
            "total",
            "done_pct",
            "escalate_pct",
            "avg_iterations",
            "avg_cost_usd",
        ):
            assert field in agg_row, f"missing field: {field}"
