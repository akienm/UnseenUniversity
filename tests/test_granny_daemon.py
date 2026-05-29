"""Tests for devices.granny.daemon — GrannyDaemon polling loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest


def _ticket(id="T-abc", status="sprint", worker="", tags=None, gate=None):
    t = {
        "id": id,
        "title": f"ticket {id}",
        "size": "S",
        "status": status,
        "tags": tags or ["Platform"],
        "worker": worker,
    }
    if gate is not None:
        t["gate"] = gate
    return t


def _run_side_effect(ticket_map: dict):
    """Build a subprocess.run side_effect that serves list then show responses.

    ticket_map: {ticket_id: ticket_dict}
    First call (list) returns text with all IDs; subsequent show calls return
    per-ticket JSON.
    """
    list_text = "\n".join(f"  ⬜ [{tid}] (S) [sprint]" for tid in ticket_map)

    def _side_effect(cmd, **kwargs):
        verb = cmd[2] if len(cmd) > 2 else ""
        if verb == "list":
            return MagicMock(returncode=0, stdout=list_text, stderr="")
        if verb == "show":
            tid = cmd[3] if len(cmd) > 3 else ""
            if tid in ticket_map:
                return MagicMock(
                    returncode=0, stdout=json.dumps(ticket_map[tid]), stderr=""
                )
            return MagicMock(returncode=1, stdout="", stderr="not found")
        return MagicMock(returncode=1, stdout="", stderr="unexpected")

    return _side_effect


class TestLoadSprintTickets:
    def test_returns_sprint_tickets(self):
        from devices.granny.daemon import _load_sprint_tickets

        t_a = _ticket("T-a", status="sprint")
        t_b = _ticket("T-b", status="pending")
        with patch(
            "subprocess.run", side_effect=_run_side_effect({"T-a": t_a, "T-b": t_b})
        ):
            tickets = _load_sprint_tickets()
        assert len(tickets) == 1
        assert tickets[0]["id"] == "T-a"

    def test_skips_gated_tickets(self):
        from devices.granny.daemon import _load_sprint_tickets

        t_a = _ticket("T-a", status="sprint")
        t_b = _ticket("T-b", status="sprint", gate="T-a")
        with patch(
            "subprocess.run", side_effect=_run_side_effect({"T-a": t_a, "T-b": t_b})
        ):
            tickets = _load_sprint_tickets()
        assert len(tickets) == 1
        assert tickets[0]["id"] == "T-a"

    def test_returns_empty_on_list_error(self):
        from devices.granny.daemon import _load_sprint_tickets

        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="queue error"),
        ):
            tickets = _load_sprint_tickets()
        assert tickets == []

    def test_returns_empty_on_exception(self):
        from devices.granny.daemon import _load_sprint_tickets

        with patch("subprocess.run", side_effect=Exception("boom")):
            tickets = _load_sprint_tickets()
        assert tickets == []


class TestTicketNeedsCC:
    def test_worker_claude_returns_true(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "claude", "tags": []}) is True

    def test_worker_cc_with_matching_tag_returns_true(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "cc", "tags": ["Platform"]}) is True

    def test_explicit_non_cc_worker_returns_false(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "nanny", "tags": ["Platform"]}) is False

    def test_no_worker_cc_tag_returns_true(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "", "tags": ["Infrastructure"]}) is True

    def test_no_worker_no_matching_tag_returns_false(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "", "tags": ["Unrelated"]}) is False


class TestGrannyDaemonRunOnce:
    def _make_daemon(self, audit_passed=True, route_ok=True):
        from devices.granny.daemon import GrannyDaemon

        daemon = GrannyDaemon.__new__(GrannyDaemon)
        daemon._dispatched_ids = set()
        daemon._total_dispatched = 0
        daemon._total_errors = 0
        daemon._last_poll = None

        audit = MagicMock()
        audit.passed = audit_passed
        audit.escalate_to_cc = True
        audit.reasons = []

        device = MagicMock()
        device.intake_ticket.return_value = audit
        device.route_ticket.return_value = (route_ok, "cc")
        daemon._device = device
        return daemon

    def test_dispatches_two_sprint_tickets(self):
        daemon = self._make_daemon()
        tickets = [_ticket("T-a"), _ticket("T-b")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()

        assert count == 2
        assert daemon._device.route_ticket.call_count == 2

    def test_deduplicates_within_cycle(self):
        daemon = self._make_daemon()
        daemon._dispatched_ids = {"T-a"}
        tickets = [_ticket("T-a"), _ticket("T-b")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()

        assert count == 1
        assert daemon._device.route_ticket.call_count == 1

    def test_dedup_blocks_immediate_re_dispatch(self):
        # After dispatching T-a in cycle 1, cycle 2 skips it (set carries over).
        # Cycle 3 can dispatch T-a again because cycle 2 produced an empty set.
        daemon = self._make_daemon()
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count1 = daemon.run_once()  # dispatches T-a
            count2 = daemon.run_once()  # T-a blocked (in _dispatched_ids from cycle 1)
            count3 = (
                daemon.run_once()
            )  # T-a eligible again (cycle 2 produced empty set)

        assert count1 == 1
        assert count2 == 0
        assert count3 == 1

    def test_skips_non_cc_tickets(self):
        daemon = self._make_daemon()
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=False),
        ):
            count = daemon.run_once()

        assert count == 0
        daemon._device.route_ticket.assert_not_called()

    def test_skips_failed_audit_with_no_escalation(self):
        daemon = self._make_daemon(audit_passed=False)
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["size too large"]
        )
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()

        assert count == 0
        daemon._device.route_ticket.assert_not_called()

    def test_escalate_to_cc_routes_despite_audit_fail(self):
        daemon = self._make_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=True, reasons=["needs cc"]
        )
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()

        assert count == 1
