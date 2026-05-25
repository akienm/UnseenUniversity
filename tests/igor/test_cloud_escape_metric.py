"""
Tests for cloud_escape_metric.py — T-cloud-escape-rate-metric.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_turn_block(turn_id: str, intent: str, tier: str) -> str:
    data = {
        "turn_id": turn_id,
        "thread_id": "web:shared",
        "ts": "2026-04-06T10:00:00",
        "input": "test input",
        "thalamus": {"ms": 5, "intent": intent, "complexity": "low"},
        "routing": {"ms": 2, "tier": tier, "reason": "test"},
    }
    return f"=== turn {turn_id} | web:shared | 2026-04-06T10:00:00 | 100ms total ===\n{json.dumps(data)}\n=== END ===\n"


def test_cloud_escape_by_category_parses_logs(tmp_path, monkeypatch):
    """_cloud_escape_by_category reads turn_trace files and buckets correctly."""
    from devices.igor.cognition import metrics

    # Write a fake turn_trace log for today
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = tmp_path / f"turn_trace.{today}.log"
    content = ""
    content += _make_turn_block("t1", "factual_question", "tier.2")  # local
    content += _make_turn_block("t2", "factual_question", "tier.3")  # cloud
    content += _make_turn_block("t3", "code_request", "tier.3.5")  # cloud
    content += _make_turn_block("t4", "code_request", "tier.2")  # local
    content += _make_turn_block("t5", "greeting", "tier.1")  # local
    log_file.write_text(content)

    with patch.object(metrics, "_LOGS_DIR", tmp_path):
        result = metrics._cloud_escape_by_category(days=1)

    assert "factual" in result
    assert result["factual"]["total"] == 2
    assert result["factual"]["cloud"] == 1
    assert result["factual"]["cloud_pct"] == 50.0

    assert "code" in result
    assert result["code"]["total"] == 2
    assert result["code"]["cloud"] == 1

    assert "_all" in result
    assert result["_all"]["total"] == 5
    assert result["_all"]["cloud"] == 2
    assert result["_all"]["cloud_pct"] == 40.0


def test_cloud_escape_rate_report_format(tmp_path, monkeypatch):
    """cloud_escape_rate_report returns a formatted string with category rows."""
    from devices.igor.cognition import metrics
    from devices.igor.tools import cloud_escape_metric

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = tmp_path / f"turn_trace.{today}.log"
    content = ""
    content += _make_turn_block("r1", "code_request", "tier.4")
    content += _make_turn_block("r2", "factual_question", "tier.2")
    log_file.write_text(content)

    with patch.object(metrics, "_LOGS_DIR", tmp_path):
        report = cloud_escape_metric.cloud_escape_rate_report(days=1, deposit=False)

    assert "Cloud Escape Rate Report" in report
    assert "code/task" in report
    assert "factual" in report
    assert "TOTAL" in report


def test_cloud_escape_rate_report_no_logs(tmp_path):
    """cloud_escape_rate_report handles empty logs gracefully."""
    from devices.igor.cognition import metrics
    from devices.igor.tools import cloud_escape_metric

    with patch.object(metrics, "_LOGS_DIR", tmp_path):
        report = cloud_escape_metric.cloud_escape_rate_report(days=7, deposit=False)

    assert "Cloud Escape Rate Report" in report


def test_cloud_escape_metric_registered():
    """Tool is registered in the registry."""
    from lab.utility_closet.registry import registry

    tool = registry.get("cloud_escape_rate_report")
    assert tool is not None
    assert tool.name == "cloud_escape_rate_report"
