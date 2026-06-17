"""
Tests for cost-to-close metric in cc_queue.py.

Covers:
- _pricing_for_model: correct pricing lookup by model name substring
- _compute_cost_usd: returns None when no log, correct USD when log present
- _format_task_line: shows cost_usd on closed tickets
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure lab/claudecode is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "devlab" / "claudecode"))
import cc_queue


# ---------------------------------------------------------------------------
# _pricing_for_model
# ---------------------------------------------------------------------------

def test_pricing_sonnet():
    p = cc_queue._pricing_for_model("claude-sonnet-4-6")
    assert p["input"] == 3.00
    assert p["output"] == 15.00


def test_pricing_haiku():
    p = cc_queue._pricing_for_model("claude-haiku-3-5")
    assert p["input"] == 0.80
    assert p["output"] == 4.00


def test_pricing_opus():
    p = cc_queue._pricing_for_model("claude-opus-4")
    assert p["input"] == 15.00
    assert p["output"] == 75.00


def test_pricing_unknown_defaults_to_sonnet():
    p = cc_queue._pricing_for_model("unknown-model-xyz")
    assert p["input"] == 3.00


# ---------------------------------------------------------------------------
# _compute_cost_usd
# ---------------------------------------------------------------------------

@pytest.fixture()
def igor_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    return tmp_path


def test_compute_cost_no_log(igor_home):
    assert cc_queue._compute_cost_usd("T-test") is None


def test_compute_cost_no_matching_entries(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    log.write_text("2026-06-11T00:00:00+00:00|T-other|1000|0|0|500|claude-sonnet-4-6\n")
    assert cc_queue._compute_cost_usd("T-test") is None


def test_compute_cost_input_only(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    # 1 000 000 input tokens at $3.00/MTok = $3.00
    log.write_text("2026-06-11T00:00:00+00:00|T-test|1000000|0|0|0|claude-sonnet-4-6\n")
    cost = cc_queue._compute_cost_usd("T-test")
    assert cost is not None
    assert abs(cost - 3.00) < 0.0001


def test_compute_cost_output_only(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    # 1 000 000 output tokens at $15.00/MTok = $15.00
    log.write_text("2026-06-11T00:00:00+00:00|T-test|0|0|0|1000000|claude-sonnet-4-6\n")
    cost = cc_queue._compute_cost_usd("T-test")
    assert abs(cost - 15.00) < 0.0001


def test_compute_cost_multiple_sprints(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    # Two sprints, each 1M output tokens = $30.00 total
    log.write_text(
        "2026-06-11T00:00:00+00:00|T-test|0|0|0|1000000|claude-sonnet-4-6\n"
        "2026-06-11T01:00:00+00:00|T-test|0|0|0|1000000|claude-sonnet-4-6\n"
    )
    cost = cc_queue._compute_cost_usd("T-test")
    assert abs(cost - 30.00) < 0.0001


def test_compute_cost_cache_tokens(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    # 1M cache_write at $3.75/MTok = $3.75
    log.write_text("2026-06-11T00:00:00+00:00|T-test|0|1000000|0|0|claude-sonnet-4-6\n")
    cost = cc_queue._compute_cost_usd("T-test")
    assert abs(cost - 3.75) < 0.0001


def test_compute_cost_haiku_model(igor_home):
    log = igor_home / "claudecode" / "sprint_tokens.log"
    log.parent.mkdir()
    # 1M input tokens at haiku $0.80/MTok = $0.80
    log.write_text("2026-06-11T00:00:00+00:00|T-test|1000000|0|0|0|claude-haiku-3-5\n")
    cost = cc_queue._compute_cost_usd("T-test")
    assert abs(cost - 0.80) < 0.0001


# ---------------------------------------------------------------------------
# _format_task_line — cost_usd display
# ---------------------------------------------------------------------------

def _make_ticket(**kwargs):
    base = {
        "id": "T-test",
        "title": "Test ticket",
        "status": "closed",
        "size": "S",
        "priority": 0.5,
        "worker": None,
        "created_by": "akien",
        "github_issue": None,
        "target_difficulty": 1,
        "epic": None,
        "gate": None,
        "role": None,
    }
    base.update(kwargs)
    return base


def test_format_task_line_shows_cost_on_closed():
    t = _make_ticket(status="closed", cost_usd=0.8312)
    line = cc_queue._format_task_line(t)
    assert "$0.83" in line


def test_format_task_line_no_cost_when_null():
    t = _make_ticket(status="closed")
    line = cc_queue._format_task_line(t)
    assert "$" not in line


def test_format_task_line_no_cost_on_sprint():
    t = _make_ticket(status="sprint", cost_usd=1.23)
    line = cc_queue._format_task_line(t)
    assert "$" not in line


def test_format_task_line_shows_cost_on_awaiting_validation():
    t = _make_ticket(status="awaiting_validation", cost_usd=0.50)
    line = cc_queue._format_task_line(t)
    assert "$0.50" in line
