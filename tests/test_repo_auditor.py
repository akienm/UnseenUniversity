"""
Tests for devices/hubert/repo_auditor.py.

Tests:
- _parse_affected_files: returns paths from Affected files section
- _parse_affected_files: returns None for TBD entries
- _parse_affected_files: returns None when section absent
- Signal 1 NO_COMMIT: raised when git finds no commits for ticket
- Signal 2 FILE_OVERLAP: raised when affected files list has no overlap with diff
- Signal 2 FILE_OVERLAP: skipped when affected files list contains TBD
- Signal 3 DIFF_MAGNITUDE: raised for M ticket with < 5 changed lines
- Signal 3 DIFF_MAGNITUDE: skipped for S-size tickets
- run_structural_audit: skips coordination tickets (Tracking/Decision/Doc tags)
- run_structural_audit: skips tickets without T- prefix
- Flag persistence: _upsert_flag is idempotent (same key upserts, not duplicates)
- review_flag: sets reviewed_at and verdict on existing flag
- review_flag: returns False when flag not found
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.hubert.repo_auditor import (
    AuditFlag,
    _audit_ticket,
    _parse_affected_files,
    _read_existing_flags,
    _upsert_flag,
    _write_flags,
    review_flag,
    run_structural_audit,
)


# ── _parse_affected_files ─────────────────────────────────────────────────────

def test_parse_affected_files_extracts_paths():
    desc = "**Affected files:** devices/granny/daemon.py, lab/claudecode/cc_queue.py"
    result = _parse_affected_files(desc)
    assert result is not None
    assert any("daemon.py" in p for p in result)


def test_parse_affected_files_returns_none_for_tbd():
    desc = "**Affected files:** TBD — discovery step in sprint"
    assert _parse_affected_files(desc) is None


def test_parse_affected_files_returns_none_when_absent():
    assert _parse_affected_files("no affected files section here") is None


def test_parse_affected_files_strips_parenthetical_notes():
    desc = "**Affected files:** devices/hubert/repo_auditor.py (new), lab/claudecode/completion_audit.py (read only)"
    result = _parse_affected_files(desc)
    assert result is not None
    assert any("repo_auditor.py" in p for p in result)


# ── Signal 1: NO_COMMIT ───────────────────────────────────────────────────────

def test_no_commit_flag_raised_when_no_commits(tmp_path):
    ticket = {"id": "T-no-commit", "size": "M", "description": "", "tags": []}

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=[]):
        flags = _audit_ticket(ticket, tmp_path)

    assert len(flags) == 1
    assert flags[0].signal == "NO_COMMIT"
    assert flags[0].severity == "HIGH"


def test_no_commit_flag_not_raised_when_commits_exist(tmp_path):
    ticket = {
        "id": "T-with-commit",
        "size": "M",
        "description": "**Affected files:** devices/foo.py",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc123"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value={"devices/foo.py"}), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=10):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "NO_COMMIT" not in signals


# ── Signal 2: FILE_OVERLAP ────────────────────────────────────────────────────

def test_file_overlap_flag_raised_when_no_overlap(tmp_path):
    ticket = {
        "id": "T-overlap",
        "size": "M",
        "description": "**Affected files:** devices/hubert/repo_auditor.py",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value={"unrelated/file.py"}), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=10):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "FILE_OVERLAP" in signals
    flag = next(f for f in flags if f.signal == "FILE_OVERLAP")
    assert flag.severity == "MED"


def test_file_overlap_skipped_when_tbd(tmp_path):
    ticket = {
        "id": "T-tbd",
        "size": "M",
        "description": "**Affected files:** TBD — discovery step in sprint",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value=set()), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=10):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "FILE_OVERLAP" not in signals


# ── Signal 3: DIFF_MAGNITUDE ──────────────────────────────────────────────────

def test_diff_magnitude_raised_for_m_below_threshold(tmp_path):
    ticket = {
        "id": "T-tiny",
        "size": "M",
        "description": "",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value=set()), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=2):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "DIFF_MAGNITUDE" in signals
    flag = next(f for f in flags if f.signal == "DIFF_MAGNITUDE")
    assert flag.severity == "LOW"


def test_diff_magnitude_not_raised_for_s_size(tmp_path):
    ticket = {
        "id": "T-small",
        "size": "S",
        "description": "",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value=set()), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=1):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "DIFF_MAGNITUDE" not in signals


def test_diff_magnitude_not_raised_above_threshold(tmp_path):
    ticket = {
        "id": "T-adequate",
        "size": "M",
        "description": "",
        "tags": [],
    }

    with patch("devices.hubert.repo_auditor._git_commits_for_ticket", return_value=["abc"]), \
         patch("devices.hubert.repo_auditor._git_changed_files", return_value=set()), \
         patch("devices.hubert.repo_auditor._git_lines_changed", return_value=20):
        flags = _audit_ticket(ticket, tmp_path)

    signals = {f.signal for f in flags}
    assert "DIFF_MAGNITUDE" not in signals


# ── run_structural_audit filters ──────────────────────────────────────────────

def test_run_structural_audit_skips_tracking_tagged_tickets(tmp_path):
    tickets = [
        {"id": "T-track", "size": "M", "description": "", "tags": ["Tracking"]},
    ]

    with patch("devices.hubert.repo_auditor._FLAGS_FILE", tmp_path / "flags.jsonl"), \
         patch("devlab.claudecode.completion_audit.get_closed_tickets", return_value=tickets), \
         patch("devices.hubert.repo_auditor._audit_ticket") as mock_audit:
        run_structural_audit(str(tmp_path))

    mock_audit.assert_not_called()


def test_run_structural_audit_skips_non_t_prefix_tickets(tmp_path):
    tickets = [
        {"id": "IGOR-123", "size": "M", "description": "", "tags": []},
    ]

    with patch("devices.hubert.repo_auditor._FLAGS_FILE", tmp_path / "flags.jsonl"), \
         patch("devlab.claudecode.completion_audit.get_closed_tickets", return_value=tickets), \
         patch("devices.hubert.repo_auditor._audit_ticket") as mock_audit:
        run_structural_audit(str(tmp_path))

    mock_audit.assert_not_called()


def test_run_structural_audit_skips_s_tickets_by_default(tmp_path):
    tickets = [
        {"id": "T-small-s", "size": "S", "description": "", "tags": []},
    ]

    with patch("devices.hubert.repo_auditor._FLAGS_FILE", tmp_path / "flags.jsonl"), \
         patch("devlab.claudecode.completion_audit.get_closed_tickets", return_value=tickets), \
         patch("devices.hubert.repo_auditor._audit_ticket") as mock_audit:
        run_structural_audit(str(tmp_path))

    mock_audit.assert_not_called()


# ── Flag persistence ──────────────────────────────────────────────────────────

def test_upsert_flag_idempotent(tmp_path):
    """Writing the same (ticket_id, signal) twice results in one entry."""
    with patch("devices.hubert.repo_auditor._FLAGS_FILE", tmp_path / "flags.jsonl"):
        flag = AuditFlag(
            ticket_id="T-dup",
            signal="NO_COMMIT",
            severity="HIGH",
            detail="test",
            checked_at="2026-06-13T00:00:00+00:00",
        )
        _upsert_flag(flag)
        _upsert_flag(flag)  # second write — should upsert, not duplicate

        index = _read_existing_flags()

    assert len(index) == 1
    assert ("T-dup", "NO_COMMIT") in index


def test_upsert_flag_updates_detail(tmp_path):
    """Second write with same key but different detail replaces the entry."""
    flags_file = tmp_path / "flags.jsonl"
    with patch("devices.hubert.repo_auditor._FLAGS_FILE", flags_file):
        flag1 = AuditFlag("T-u", "NO_COMMIT", "HIGH", "old detail", "2026-01-01T00:00:00+00:00")
        flag2 = AuditFlag("T-u", "NO_COMMIT", "HIGH", "new detail", "2026-06-13T00:00:00+00:00")
        _upsert_flag(flag1)
        _upsert_flag(flag2)

        index = _read_existing_flags()

    assert index[("T-u", "NO_COMMIT")]["detail"] == "new detail"


# ── review_flag ────────────────────────────────────────────────────────────────

def test_review_flag_sets_reviewed_at_and_verdict(tmp_path):
    """review_flag sets reviewed_at and verdict on the matching entry."""
    flags_file = tmp_path / "flags.jsonl"
    with patch("devices.hubert.repo_auditor._FLAGS_FILE", flags_file):
        flag = AuditFlag("T-rv", "NO_COMMIT", "HIGH", "detail", "2026-06-13T00:00:00+00:00")
        _upsert_flag(flag)

        result = review_flag("T-rv", "NO_COMMIT", "dismiss")
        index = _read_existing_flags()

    assert result is True
    entry = index[("T-rv", "NO_COMMIT")]
    assert entry["verdict"] == "dismiss"
    assert entry.get("reviewed_at") is not None


def test_review_flag_returns_false_when_not_found(tmp_path):
    """review_flag returns False when (ticket_id, signal) has no matching entry."""
    with patch("devices.hubert.repo_auditor._FLAGS_FILE", tmp_path / "flags.jsonl"):
        result = review_flag("T-missing", "NO_COMMIT", "confirm")
    assert result is False


# ── Nanny cron entry ───────────────────────────────────────────────────────────

def test_nanny_default_schedule_includes_repo_audit():
    """nightly_repo_audit appears in Nanny Ogg's default schedule."""
    from devices.nanny.device import _DEFAULT_SCHEDULE
    ids = {e["entry_id"] for e in _DEFAULT_SCHEDULE}
    assert "nightly_repo_audit" in ids
    entry = next(e for e in _DEFAULT_SCHEDULE if e["entry_id"] == "nightly_repo_audit")
    assert entry["action_type"] == "run_repo_audit"
    assert entry["condition_params"].get("hour") == 3
