"""Tests for T-cc-queue-scraps-gate — Scraps pre-flight in cmd_add and cmd_claim.

Four cases from the test plan:
  1. add with empty description → Scraps issues printed, ticket not added
  2. valid ticket add → passes, metadata contains scraps_validated timestamp
  3. Scraps offline during claim → warning printed, transition proceeds
  4. claim on already-validated ticket → Scraps not called (no redundant call)

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

from lab.claudecode.cc_queue import _scraps_validate, cmd_add, cmd_claim


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


class TestCmdClaimScrapsGate:
    def _seed_and_claim(
        self, ticket: dict, claim_args: list[str] | None = None
    ) -> tuple[list, str, int]:
        """Seed a ticket then call cmd_claim; return (tasks, stdout, exit_code)."""
        import copy

        tasks = [copy.deepcopy(ticket)]
        saved: list[dict] = []
        exit_code = 0

        def mock_load():
            return list(tasks)

        def mock_save(ts):
            saved.extend(copy.deepcopy(ts))

        buf = StringIO()
        args = claim_args or [ticket["id"], "--as", "claude"]
        try:
            with (
                patch("lab.claudecode.cc_queue._load", mock_load),
                patch("lab.claudecode.cc_queue._save", mock_save),
                patch("lab.claudecode.cc_queue._log"),
                patch("sys.stdout", buf),
            ):
                cmd_claim(args)
        except SystemExit as e:
            exit_code = int(e.code or 0)

        return saved, buf.getvalue(), exit_code

    def test_claim_offline_scraps_proceeds(self):
        ticket = _make_ticket()
        with patch(
            "devices.scraps.scraps_device.ScrapsDevice",
            side_effect=Exception("offline"),
        ):
            saved, out, code = self._seed_and_claim(ticket)
        assert code == 0, f"offline Scraps must not block claim, out={out!r}"
        assert "Scraps offline" in out
        assert any(t.get("status") == "in_progress" for t in saved)

    def test_already_validated_skips_scraps_call(self):
        """If scraps_validated is set, ScrapsDevice must not be called."""
        ticket = _make_ticket(scraps_validated="2026-05-19T12:00:00+00:00")
        mock_device = MagicMock()
        with patch(
            "devices.scraps.scraps_device.ScrapsDevice", return_value=mock_device
        ):
            saved, out, code = self._seed_and_claim(ticket)
        mock_device.validate_ticket.assert_not_called()
        assert code == 0
        assert any(t.get("status") == "in_progress" for t in saved)

    def test_invalid_ticket_blocks_claim(self):
        ticket = _make_ticket(description="")
        saved, out, code = self._seed_and_claim(ticket)
        assert code != 0, "invalid ticket must block claim"
        assert "Scraps validation failed" in out
        assert not any(t.get("status") == "in_progress" for t in saved)
