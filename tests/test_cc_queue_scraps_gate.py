"""Tests for T-cc-queue-scraps-gate — Scraps pre-flight in cmd_add.

Scraps validation lives entirely in cmd_add — never in cmd_claim (removed).
Four cases from the test plan:
  1. add with empty description → Scraps issues printed, ticket not added
  2. valid ticket add → passes, metadata contains scraps_validated timestamp
  3. Scraps offline during add → warning printed, ticket still added
  4. cmd_claim always raises LegacyDirectClaimError (claim path removed)

# author-model: claude-sonnet-4-6
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lab.claudecode.cc_queue import (
    LegacyDirectClaimError,
    _scraps_validate,
    cmd_add,
    cmd_claim,
)


def _make_ticket(**kwargs) -> dict:
    base = {
        "id": "T-test-scraps-gate",
        "title": "Wire Scraps gate into queue transitions",
        "description": (
            "**Affected files:** cc_queue.py\n"
            "**Test plan:** integration test covering add and claim paths."
        ),
        "size": "S",
        "status": "sprint",
        "worker": "claude",
        "tags": [],
    }
    base.update(kwargs)
    return base


class TestScrapsValidateHelper:
    def test_invalid_ticket_returns_false_and_prints_issues(self, capsys):
        ticket = {"id": "T-x", "title": "Test ticket", "description": ""}
        result = _scraps_validate(ticket)
        assert result is False
        out = capsys.readouterr().out
        assert "Scraps validation failed" in out
        assert "description is empty" in out

    def test_valid_ticket_returns_true_and_stamps_scraps_validated(self):
        ticket = _make_ticket()
        result = _scraps_validate(ticket)
        assert result is True
        assert "scraps_validated" in ticket
        assert ticket["scraps_validated"]  # non-empty ISO timestamp

    def test_offline_returns_true_and_prints_warning(self, capsys):
        with patch(
            "devices.scraps.scraps_device.ScrapsDevice",
            side_effect=Exception("connection refused"),
        ):
            ticket = _make_ticket()
            result = _scraps_validate(ticket)
        assert result is True
        out = capsys.readouterr().out
        assert "Scraps offline" in out
        assert "validation skipped" in out


class TestCmdAddScrapsGate:
    def _run_add(self, ticket: dict) -> tuple[list, str]:
        """Call cmd_add with a mocked DB; return (saved_tasks, stdout)."""
        import json

        saved: list[dict] = []

        def mock_load():
            return []

        def mock_save(tasks):
            saved.extend(tasks)

        buf = StringIO()
        with (
            patch("lab.claudecode.cc_queue._load", mock_load),
            patch("lab.claudecode.cc_queue._save", mock_save),
            patch("lab.claudecode.cc_queue._log"),
            patch("sys.stdout", buf),
        ):
            cmd_add([json.dumps(ticket)])

        return saved, buf.getvalue()

    def test_empty_description_blocked_with_issue_list(self):
        ticket = _make_ticket(description="")
        saved, out = self._run_add(ticket)
        assert not saved, "empty-description ticket should not be saved"
        assert "Scraps validation failed" in out
        assert "description is empty" in out
        assert "blocked" in out

    def test_valid_ticket_added_with_scraps_validated(self):
        ticket = _make_ticket()
        saved, out = self._run_add(ticket)
        assert len(saved) == 1, f"expected 1 saved ticket, got {len(saved)}"
        assert "scraps_validated" in saved[0]
        assert saved[0]["scraps_validated"]
        assert "added" in out

    def test_scraps_offline_ticket_still_added(self):
        ticket = _make_ticket()
        with patch(
            "devices.scraps.scraps_device.ScrapsDevice",
            side_effect=Exception("offline"),
        ):
            saved, out = self._run_add(ticket)
        assert len(saved) == 1, "offline Scraps must not block add"
        assert "Scraps offline" in out


class TestCmdClaimRemoved:
    """cmd_claim is removed — always raises LegacyDirectClaimError.

    Scraps validation now lives entirely in cmd_add (at add-time).
    Workers receive tickets only via CC dispatch: cc_queue.py dispatch <ticket-id>
    """

    def test_cmd_claim_always_raises(self):
        """cmd_claim raises LegacyDirectClaimError unconditionally."""
        with patch("lab.claudecode.cc_queue._igor_post", return_value=False):
            with pytest.raises(LegacyDirectClaimError) as exc_info:
                cmd_claim(["T-test-scraps-gate"])
        assert "dispatch" in str(exc_info.value)

    def test_cmd_claim_raises_even_with_valid_ticket(self):
        """cmd_claim raises even when the ticket would otherwise be valid."""
        with patch("lab.claudecode.cc_queue._igor_post", return_value=False):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-test-scraps-gate", "--as", "claude"])
