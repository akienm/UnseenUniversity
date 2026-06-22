"""
Tests for devlab/claudecode/cc_nightly_context_prep.py.

Tests:
- build_briefing: includes in-flight from slate
- build_briefing: includes done-today lines
- build_briefing: includes high-priority tickets from DB
- build_briefing: includes design tickets
- build_briefing: includes pending approvals
- build_briefing: contains tomorrow date header
- write_context_brief: dry_run prints and returns True
- write_context_brief: writes palace node via DB (mocked)
- write_context_brief: returns False and prints warning on DB error
- run: end-to-end dry_run returns expected summary keys
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.cc_nightly_context_prep import (
    _read_slate_section,
    build_briefing,
    run,
    write_context_brief,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _redirect_memory_root(tmp_path, monkeypatch):
    """Point the slate_store resolver at the test's tmp_path (UU_MEMORY_ROOT)."""
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))


def _write_slate(tmp_path: Path, date: str, content: str) -> None:
    slate_dir = tmp_path / "slates"
    slate_dir.mkdir(exist_ok=True)
    datestamp = date.replace("-", "")
    (slate_dir / f"{datestamp}.slate.txt").write_text(content, encoding="utf-8")


_SLATE_CONTENT = """\
# Slate 2026-06-13

## Notes
Remember to check the DB.

## In-flight
T-foo: working on the widget

## Planned
- T-bar: next thing

## Done today
- T-baz: shipped the gadget
- T-qux: fixed the throbber
"""


# ── _read_slate_section ───────────────────────────────────────────────────────

def test_read_slate_section_returns_in_flight(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path):
        result = _read_slate_section("2026-06-13", "In-flight")
    assert "T-foo" in result


def test_read_slate_section_returns_done_today(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path):
        result = _read_slate_section("2026-06-13", "Done today")
    assert "T-baz" in result


def test_read_slate_section_missing_returns_empty(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path):
        result = _read_slate_section("2026-06-13", "Nonexistent Section")
    assert result == ""


def test_read_slate_section_missing_file_returns_empty(tmp_path):
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path):
        result = _read_slate_section("2026-06-13", "In-flight")
    assert result == ""


# ── build_briefing ────────────────────────────────────────────────────────────

def test_build_briefing_contains_tomorrow_header(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "2026-06-14" in brief


def test_build_briefing_includes_in_flight(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "T-foo" in brief


def test_build_briefing_includes_done_today(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "T-baz" in brief


def test_build_briefing_includes_high_priority_tickets(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    hp = [{"id": "T-urgent", "title": "Do the thing", "status": "sprint", "size": "M", "priority": 0.9}]
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=hp), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "T-urgent" in brief
    assert "Do the thing" in brief


def test_build_briefing_includes_design_tickets(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    design = [{"id": "T-design-me", "title": "Needs design"}]
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=design), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "T-design-me" in brief


def test_build_briefing_includes_pending_approvals(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    approvals = [{"id": "T-approve-me", "title": "Awaiting sign-off"}]
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=approvals), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        brief = build_briefing("2026-06-13")
    assert "T-approve-me" in brief


def test_build_briefing_includes_patterns(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    patterns = [{"path": "palace.patterns.observability-first", "title": "Observability-first design"}]
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=patterns):
        brief = build_briefing("2026-06-13")
    assert "Observability-first design" in brief


# ── write_context_brief ───────────────────────────────────────────────────────

def test_write_context_brief_dry_run(tmp_path, capsys):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        result = write_context_brief("2026-06-13", dry_run=True)
    out = capsys.readouterr().out
    assert result is True
    assert "[DRY RUN]" in out
    assert "palace.sessions." in out


def test_write_context_brief_calls_db(tmp_path):
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)

    with patch("psycopg2.connect", return_value=mock_conn), \
         patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        result = write_context_brief("2026-06-13", dry_run=False)

    assert result is True
    mock_conn.commit.assert_called_once()


def test_write_context_brief_db_failure_returns_false(tmp_path, capsys):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("psycopg2.connect", side_effect=Exception("DB down")), \
         patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        result = write_context_brief("2026-06-13", dry_run=False)
    assert result is False
    err = capsys.readouterr().err
    assert "context brief write failed" in err


# ── run (end-to-end) ──────────────────────────────────────────────────────────

def test_run_dry_run_returns_summary(tmp_path):
    _write_slate(tmp_path, "2026-06-13", _SLATE_CONTENT)
    with patch("devlab.claudecode.cc_nightly_context_prep._IGOR_HOME", tmp_path), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_high_priority_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_design_tickets", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_pending_approvals", return_value=[]), \
         patch("devlab.claudecode.cc_nightly_context_prep._read_recent_patterns", return_value=[]):
        summary = run(date="2026-06-13", dry_run=True)

    assert "date" in summary
    assert "tomorrow" in summary
    assert "context_brief_written" in summary
    assert summary["date"] == "2026-06-13"
    assert summary["tomorrow"] == "2026-06-14"
    assert summary["context_brief_written"] is True
