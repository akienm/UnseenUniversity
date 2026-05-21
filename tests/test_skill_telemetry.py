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
