"""Tests for devlab/claudecode/completion_audit.py data layer."""
import json
from pathlib import Path

import pytest


def test_extract_criteria_present():
    from devlab.claudecode.completion_audit import extract_criteria

    desc = (
        "Some description.\n\n"
        "**Completion criteria:** The file exists and contains the pattern; "
        "tests pass on first run.\n\n"
        "**Other field:** something else"
    )
    result = extract_criteria(desc)
    assert result is not None
    assert "The file exists" in result
    assert "Other field" not in result


def test_extract_criteria_absent():
    from devlab.claudecode.completion_audit import extract_criteria

    desc = "A description with no completion criteria section."
    assert extract_criteria(desc) is None


def test_extract_criteria_empty_section():
    from devlab.claudecode.completion_audit import extract_criteria

    desc = "**Completion criteria:**\n\n**Design rules:** something"
    assert extract_criteria(desc) is None


def test_extract_criteria_multiline():
    from devlab.claudecode.completion_audit import extract_criteria

    desc = (
        "**Completion criteria:** Line one;\n"
        "line two details;\n"
        "line three.\n"
        "**Design rules:** abc"
    )
    result = extract_criteria(desc)
    assert result is not None
    assert "Line one" in result
    assert "line two" in result
    assert "Design rules" not in result


def test_log_result_creates_file(tmp_path, monkeypatch):
    from devlab.claudecode import completion_audit as ca

    monkeypatch.setattr(ca, "AUDIT_LOG", tmp_path / "completion_audit.log")
    ca.log_result("T-test-ticket", "pass", "all criteria met in repo")

    log = tmp_path / "completion_audit.log"
    assert log.exists()
    entry = json.loads(log.read_text().strip())
    assert entry["ticket_id"] == "T-test-ticket"
    assert entry["verdict"] == "pass"
    assert "criteria" in entry["reason"]


def test_log_result_appends(tmp_path, monkeypatch):
    from devlab.claudecode import completion_audit as ca

    monkeypatch.setattr(ca, "AUDIT_LOG", tmp_path / "completion_audit.log")
    ca.log_result("T-aaa", "pass", "ok")
    ca.log_result("T-bbb", "fail", "file missing")

    lines = (tmp_path / "completion_audit.log").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ticket_id"] == "T-aaa"
    assert json.loads(lines[1])["ticket_id"] == "T-bbb"
    assert json.loads(lines[1])["verdict"] == "fail"


def test_read_results_empty(tmp_path, monkeypatch):
    from devlab.claudecode import completion_audit as ca

    monkeypatch.setattr(ca, "AUDIT_LOG", tmp_path / "no_log.log")
    assert ca.read_results(days=7) == []


def test_read_results_filters_old(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from devlab.claudecode import completion_audit as ca

    monkeypatch.setattr(ca, "AUDIT_LOG", tmp_path / "completion_audit.log")
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    log = tmp_path / "completion_audit.log"
    log.write_text(
        json.dumps({"ts": old_ts, "ticket_id": "T-old", "verdict": "pass", "reason": "old"}) + "\n"
        + json.dumps({"ts": new_ts, "ticket_id": "T-new", "verdict": "fail", "reason": "new"}) + "\n"
    )

    results = ca.read_results(days=7)
    assert len(results) == 1
    assert results[0]["ticket_id"] == "T-new"
