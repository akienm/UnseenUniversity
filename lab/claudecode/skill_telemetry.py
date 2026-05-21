"""
skill_telemetry.py — Contract schema + logger for per-skill behavioral telemetry.

Two concerns CC needs telemetry for:
  1. CC forgets rules ("forget-flags"): each skill declares the behavioral
     rules it depends on. Violations get appended to violation_log.jsonl.
  2. Improvement metrics ("outcome metrics"): each skill declares success
     postconditions. Outcomes get appended to outcome_log.jsonl.

Storage (JSONL, no Postgres dependency):
  $IGOR_HOME/claudecode/violation_log.jsonl
  $IGOR_HOME/claudecode/outcome_log.jsonl

Palace node at theigors/skill-telemetry/schema carries the canonical spec.

T-skill-telemetry-schema — schema dataclasses + path helpers
T-skill-telemetry-logger — append_violation / append_outcome / query functions
T-skill-telemetry-contracts — per-skill contracts for the 15 core skills
T-skill-telemetry-rollup — surfaces top violations into context-load
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _igor_home() -> Path:
    return Path(os.environ.get("IGOR_HOME", Path.home() / ".TheIgors"))


def violation_log_path() -> Path:
    return _igor_home() / "claudecode" / "violation_log.jsonl"


def outcome_log_path() -> Path:
    return _igor_home() / "claudecode" / "outcome_log.jsonl"


@dataclass
class ForgotFlag:
    name: str  # slug — e.g. "always-run-tests-before-commit"
    description: str  # what CC must do (positive-target framing)
    check_hint: str  # how a reviewer can detect a violation post-hoc


@dataclass
class ImprovementMetric:
    name: str  # slug — e.g. "tests-pass-after-sprint"
    description: str  # one sentence: what success looks like
    default_value: bool = False


@dataclass
class SkillContract:
    skill: str
    forget_flags: list[ForgotFlag] = field(default_factory=list)
    improvement_metrics: list[ImprovementMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "forget_flags": [
                {
                    "name": f.name,
                    "description": f.description,
                    "check_hint": f.check_hint,
                }
                for f in self.forget_flags
            ],
            "improvement_metrics": [
                {
                    "name": m.name,
                    "description": m.description,
                    "default_value": m.default_value,
                }
                for m in self.improvement_metrics
            ],
        }


@dataclass
class ViolationRecord:
    skill: str
    flag_name: str  # which ForgotFlag was violated
    context: str  # brief description of what happened
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "skill": self.skill,
            "flag_name": self.flag_name,
            "context": self.context,
            "session_id": self.session_id,
        }


@dataclass
class OutcomeRecord:
    skill: str
    postconditions_met: dict[str, bool]  # metric_name → pass/fail
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "skill": self.skill,
            "postconditions_met": self.postconditions_met,
            "session_id": self.session_id,
        }


# ── Logger ─────────────────────────────────────────────────────────────────────


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _cutoff_ts(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def append_violation(
    skill: str,
    flag_name: str,
    context: str,
    session_id: str = "",
) -> ViolationRecord:
    """Append a violation record to violation_log.jsonl."""
    rec = ViolationRecord(
        skill=skill,
        flag_name=flag_name,
        context=context,
        session_id=session_id,
    )
    _append_jsonl(violation_log_path(), rec.to_dict())
    return rec


def append_outcome(
    skill: str,
    postconditions_met: dict[str, bool],
    session_id: str = "",
) -> OutcomeRecord:
    """Append an outcome record to outcome_log.jsonl."""
    rec = OutcomeRecord(
        skill=skill,
        postconditions_met=postconditions_met,
        session_id=session_id,
    )
    _append_jsonl(outcome_log_path(), rec.to_dict())
    return rec


def top_violations(n: int = 10, days: int = 30) -> list[tuple[str, str, int]]:
    """Return top N (skill, flag_name) pairs by frequency over the last `days` days.

    Each entry is (skill, flag_name, count), sorted by count descending.
    """
    cutoff = _cutoff_ts(days)
    records = _read_jsonl(violation_log_path())
    counter: Counter[tuple[str, str]] = Counter()
    for r in records:
        if r.get("ts", "") >= cutoff:
            key = (r.get("skill", ""), r.get("flag_name", ""))
            counter[key] += 1
    return [(skill, flag, count) for (skill, flag), count in counter.most_common(n)]


def skill_outcome_trend(skill: str, days: int = 30) -> dict[str, list[bool]]:
    """Return per-metric pass/fail history for a skill over the last `days` days.

    Returns dict[metric_name, list[bool]] — chronological order.
    """
    cutoff = _cutoff_ts(days)
    records = _read_jsonl(outcome_log_path())
    trend: dict[str, list[bool]] = {}
    for r in records:
        if r.get("skill") == skill and r.get("ts", "") >= cutoff:
            for metric, passed in r.get("postconditions_met", {}).items():
                trend.setdefault(metric, []).append(bool(passed))
    return trend


def monthly_rollup(n: int = 10) -> list[tuple[str, str, int]]:
    """Return top N forget-flags by frequency over the last 30 days."""
    return top_violations(n=n, days=30)
