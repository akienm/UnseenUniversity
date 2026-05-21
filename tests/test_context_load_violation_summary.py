"""
test_context_load_violation_summary.py — Tests for violation-summary action in context-load/run.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest


def _load_run(name: str) -> object:
    run_path = str(Path(__file__).parent.parent / "skills" / name / "run")
    loader = importlib.machinery.SourceFileLoader(f"{name}_run", run_path)
    spec = importlib.util.spec_from_loader(f"{name}_run", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_cl_run = _load_run("context-load")


def _write_violations(log_path: Path, entries: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_violation_summary_empty_log_is_silent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    _cl_run.violation_summary()
    out = capsys.readouterr().out
    assert out == ""


def test_violation_summary_absent_log_is_silent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    log = tmp_path / "claudecode" / "violation_log.jsonl"
    assert not log.exists()
    _cl_run.violation_summary()
    out = capsys.readouterr().out
    assert out == ""


def test_violation_summary_shows_top_violations(monkeypatch, tmp_path, capsys):
    from datetime import datetime, timezone

    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    now = datetime.now(timezone.utc).isoformat()
    log = tmp_path / "claudecode" / "violation_log.jsonl"
    _write_violations(
        log,
        [
            {
                "ts": now,
                "skill": "sprint-ticket",
                "flag_name": "always-run-tests",
                "context": "",
                "session_id": "",
            },
            {
                "ts": now,
                "skill": "sprint-ticket",
                "flag_name": "always-run-tests",
                "context": "",
                "session_id": "",
            },
            {
                "ts": now,
                "skill": "decided",
                "flag_name": "hypothesis-first",
                "context": "",
                "session_id": "",
            },
        ],
    )
    _cl_run.violation_summary(n=5, days=7)
    out = capsys.readouterr().out
    assert "Most-forgotten rules" in out
    assert "sprint-ticket/always-run-tests" in out
    assert "2×" in out


def test_violation_summary_respects_days_window(monkeypatch, tmp_path, capsys):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    recent_ts = datetime.now(timezone.utc).isoformat()
    log = tmp_path / "claudecode" / "violation_log.jsonl"
    _write_violations(
        log,
        [
            {
                "ts": old_ts,
                "skill": "old",
                "flag_name": "stale-flag",
                "context": "",
                "session_id": "",
            },
            {
                "ts": recent_ts,
                "skill": "new",
                "flag_name": "recent-flag",
                "context": "",
                "session_id": "",
            },
        ],
    )
    _cl_run.violation_summary(n=5, days=30)
    out = capsys.readouterr().out
    assert "old/stale-flag" not in out
    assert "new/recent-flag" in out
