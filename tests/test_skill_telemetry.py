"""
test_skill_telemetry.py — Schema shape tests for skill_telemetry module.

Covers:
  - SkillContract / ForgotFlag / ImprovementMetric construction and to_dict
  - ViolationRecord / OutcomeRecord construction and to_dict
  - Log path helpers respect IGOR_HOME env var
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab.claudecode.skill_telemetry import (
    ForgotFlag,
    ImprovementMetric,
    OutcomeRecord,
    SkillContract,
    ViolationRecord,
    outcome_log_path,
    violation_log_path,
)


def test_skill_contract_to_dict_shape():
    contract = SkillContract(
        skill="sprint-ticket",
        forget_flags=[
            ForgotFlag(
                name="always-run-tests-before-commit",
                description="Run tests before every commit.",
                check_hint="Check git log for commits without preceding test run.",
            )
        ],
        improvement_metrics=[
            ImprovementMetric(
                name="tests-pass-after-sprint",
                description="All tests pass when the ticket closes.",
            )
        ],
    )
    d = contract.to_dict()
    assert d["skill"] == "sprint-ticket"
    assert len(d["forget_flags"]) == 1
    assert d["forget_flags"][0]["name"] == "always-run-tests-before-commit"
    assert len(d["improvement_metrics"]) == 1
    assert d["improvement_metrics"][0]["name"] == "tests-pass-after-sprint"
    assert d["improvement_metrics"][0]["default_value"] is False


def test_skill_contract_empty():
    contract = SkillContract(skill="savestate")
    d = contract.to_dict()
    assert d["skill"] == "savestate"
    assert d["forget_flags"] == []
    assert d["improvement_metrics"] == []


def test_violation_record_to_dict_shape():
    rec = ViolationRecord(
        skill="sprint-ticket",
        flag_name="always-run-tests-before-commit",
        context="Committed without running tests — saw 'git commit' with no prior test call.",
        session_id="session-abc",
    )
    d = rec.to_dict()
    assert d["skill"] == "sprint-ticket"
    assert d["flag_name"] == "always-run-tests-before-commit"
    assert d["session_id"] == "session-abc"
    assert "ts" in d


def test_outcome_record_to_dict_shape():
    rec = OutcomeRecord(
        skill="sprint-ticket",
        postconditions_met={
            "tests-pass-after-sprint": True,
            "ticket-closed-cleanly": False,
        },
        session_id="session-xyz",
    )
    d = rec.to_dict()
    assert d["skill"] == "sprint-ticket"
    assert d["postconditions_met"]["tests-pass-after-sprint"] is True
    assert d["postconditions_met"]["ticket-closed-cleanly"] is False


def test_log_paths_respect_igor_home(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    assert violation_log_path() == tmp_path / "claudecode" / "violation_log.jsonl"
    assert outcome_log_path() == tmp_path / "claudecode" / "outcome_log.jsonl"


def test_log_paths_default_to_theigors(monkeypatch):
    monkeypatch.delenv("IGOR_HOME", raising=False)
    vp = violation_log_path()
    op = outcome_log_path()
    assert vp.name == "violation_log.jsonl"
    assert op.name == "outcome_log.jsonl"
    assert "claudecode" in str(vp)


# ── Logger tests ───────────────────────────────────────────────────────────────


from lab.claudecode.skill_telemetry import (
    append_outcome,
    append_violation,
    monthly_rollup,
    skill_outcome_trend,
    top_violations,
)


def test_append_violation_creates_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    rec = append_violation(
        "sprint-ticket", "always-run-tests", "skipped tests before commit"
    )
    log = tmp_path / "claudecode" / "violation_log.jsonl"
    assert log.exists()
    import json

    line = json.loads(log.read_text().strip())
    assert line["skill"] == "sprint-ticket"
    assert line["flag_name"] == "always-run-tests"
    assert line["context"] == "skipped tests before commit"
    assert "ts" in line


def test_append_outcome_creates_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    append_outcome("sprint-ticket", {"tests-green": True, "diff-clean": False})
    log = tmp_path / "claudecode" / "outcome_log.jsonl"
    assert log.exists()
    import json

    line = json.loads(log.read_text().strip())
    assert line["skill"] == "sprint-ticket"
    assert line["postconditions_met"]["tests-green"] is True
    assert line["postconditions_met"]["diff-clean"] is False


def test_top_violations_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    append_violation("decided", "hypothesis-first", "skipped")
    append_violation("decided", "hypothesis-first", "skipped again")
    append_violation("sprint-ticket", "run-tests", "skipped tests")

    results = top_violations(n=5, days=7)
    assert len(results) == 2
    top_skill, top_flag, top_count = results[0]
    assert top_skill == "decided"
    assert top_flag == "hypothesis-first"
    assert top_count == 2


def test_top_violations_excludes_old_records(monkeypatch, tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    log = tmp_path / "claudecode" / "violation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    log.write_text(
        json.dumps(
            {
                "ts": old_ts,
                "skill": "old",
                "flag_name": "stale",
                "context": "",
                "session_id": "",
            }
        )
        + "\n"
    )
    append_violation("new-skill", "recent-flag", "recent")
    results = top_violations(n=5, days=30)
    skills = [r[0] for r in results]
    assert "old" not in skills
    assert "new-skill" in skills


def test_skill_outcome_trend(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    append_outcome("sprint-ticket", {"tests-green": True})
    append_outcome("sprint-ticket", {"tests-green": False})
    append_outcome("other-skill", {"tests-green": True})

    trend = skill_outcome_trend("sprint-ticket", days=7)
    assert "tests-green" in trend
    assert trend["tests-green"] == [True, False]
    assert "other-skill" not in str(trend)


def test_monthly_rollup_delegates_to_top_violations(monkeypatch, tmp_path):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    append_violation("decided", "hypothesis-first", "skipped")
    results = monthly_rollup(n=5)
    assert len(results) >= 1
    assert results[0][0] == "decided"
