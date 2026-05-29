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


def _make_bare_daemon(audit_passed=True, route_ok=True):
    """Construct a GrannyDaemon bypassing __init__, with mocked device and IMAP."""
    from devices.granny.daemon import GrannyDaemon

    daemon = GrannyDaemon.__new__(GrannyDaemon)
    daemon._dispatched_ids = set()
    daemon._alerted_ids = set()
    daemon._total_dispatched = 0
    daemon._total_errors = 0
    daemon._last_poll = None
    daemon._imap = MagicMock()

    audit = MagicMock()
    audit.passed = audit_passed
    audit.escalate_to_cc = True
    audit.reasons = []

    device = MagicMock()
    device.intake_ticket.return_value = audit
    device.route_ticket.return_value = (route_ok, "cc")
    daemon._device = device
    return daemon


class TestGrannyDaemonRunOnce:
    def _make_daemon(self, audit_passed=True, route_ok=True):
        return _make_bare_daemon(audit_passed=audit_passed, route_ok=route_ok)

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


class TestGrannyDaemonAlertCC:
    def test_alert_sends_envelope_to_cc0(self):
        daemon = _make_bare_daemon()
        daemon._alert_cc("T-foo", "missing section", "audit_fail")
        daemon._imap.append.assert_called_once()
        mailbox, envelope = daemon._imap.append.call_args[0]
        assert mailbox == "CC.0"
        assert envelope.to_device == "CC.0"
        assert envelope.from_device == "Granny.0"
        assert envelope.payload["ticket_id"] == "T-foo"
        assert envelope.payload["kind"] == "audit_fail"

    def test_alert_deduplicates_same_ticket_and_kind(self):
        daemon = _make_bare_daemon()
        daemon._alert_cc("T-foo", "reason", "audit_fail")
        daemon._alert_cc("T-foo", "reason", "audit_fail")
        daemon._imap.append.assert_called_once()

    def test_alert_different_kinds_both_sent(self):
        daemon = _make_bare_daemon()
        daemon._alert_cc("T-foo", "reason", "audit_fail")
        daemon._alert_cc("T-foo", "reason", "route_fail")
        assert daemon._imap.append.call_count == 2

    def test_alert_imap_error_does_not_raise(self):
        daemon = _make_bare_daemon()
        daemon._imap.append.side_effect = Exception("dovecot down")
        daemon._alert_cc("T-foo", "reason", "audit_fail")  # must not propagate

    def test_alert_skipped_when_imap_none(self):
        daemon = _make_bare_daemon()
        daemon._imap = None
        daemon._alert_cc("T-foo", "reason", "audit_fail")  # must not raise

    def test_run_once_alerts_on_audit_fail(self):
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["missing section"]
        )
        tickets = [_ticket("T-bad")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            daemon.run_once()
        # append called for CC.0 alert + feeds/granny publish
        cc_calls = [c for c in daemon._imap.append.call_args_list if c[0][0] == "CC.0"]
        assert len(cc_calls) == 1
        envelope = cc_calls[0][0][1]
        assert envelope.payload["kind"] == "audit_fail"
        assert envelope.payload["ticket_id"] == "T-bad"

    def test_run_once_alerts_on_route_fail(self):
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=True, escalate_to_cc=False, reasons=[]
        )
        daemon._device.route_ticket.return_value = (False, "cc")
        tickets = [_ticket("T-route-fail")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            daemon.run_once()
        # append called for CC.0 alert + feeds/granny publish
        cc_calls = [c for c in daemon._imap.append.call_args_list if c[0][0] == "CC.0"]
        assert len(cc_calls) == 1
        envelope = cc_calls[0][0][1]
        assert envelope.payload["kind"] == "route_fail"
        assert envelope.payload["ticket_id"] == "T-route-fail"

    def test_audit_fail_alert_not_duplicated_across_cycles(self):
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["bad"]
        )
        tickets = [_ticket("T-repeat")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            daemon.run_once()
            daemon.run_once()
        # CC.0 alert is deduped — fires only once; feeds/granny publishes each cycle
        cc_calls = [c for c in daemon._imap.append.call_args_list if c[0][0] == "CC.0"]
        assert len(cc_calls) == 1
