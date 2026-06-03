"""Tests for devices.granny.daemon — GrannyDaemon polling loop."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def _no_live_dispatch(monkeypatch):
    """Prevent tests from firing real tmux commands to live CC/DS sessions.

    Without this, _cc0_available() reads granny.yaml + semaphore files and can
    return True on a developer machine with claude-main running, causing tests
    to send /sprint-ticket T-bad (etc.) to the live session via tmux send-keys.
    """
    monkeypatch.setattr("devices.granny.daemon._cc0_available", lambda: False)
    monkeypatch.setattr("devices.granny.daemon._dicksimnel_available", lambda: False)


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


def _make_pg_conn(rows):
    """Build a mock psycopg2 connection that returns the given row dicts."""
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"metadata": r} for r in rows]
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


class TestLoadSprintTickets:
    def test_returns_sprint_tickets(self):
        from devices.granny.daemon import _load_sprint_tickets

        t_a = _ticket("T-a", status="sprint")
        conn = _make_pg_conn([t_a])
        with patch("psycopg2.connect", return_value=conn):
            tickets = _load_sprint_tickets()
        assert len(tickets) == 1
        assert tickets[0]["id"] == "T-a"

    def test_returns_empty_on_exception(self):
        from devices.granny.daemon import _load_sprint_tickets

        with patch("psycopg2.connect", side_effect=Exception("db down")):
            tickets = _load_sprint_tickets()
        assert tickets == []


class TestTicketNeedsCC:
    """_ticket_needs_cc: only 'minion'-tagged tickets go to inference.
    Everything else (worker=claude, worker=igor, worker unset) → CC.
    Igor coding is retired — worker=igor is treated as worker=claude.
    """

    def test_worker_claude_returns_true(self):
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "claude", "tags": []}) is True

    def test_worker_igor_returns_true(self):
        # Igor coding retired — igor-assigned tickets go to CC, not inference
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "igor", "tags": ["Memory"]}) is True

    def test_no_worker_returns_true(self):
        # Unassigned tickets default to CC
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "", "tags": ["Infrastructure"]}) is True

    def test_unrecognised_tag_returns_true(self):
        # Unknown tags fall through to CC, not inference
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "", "tags": ["Unrelated"]}) is True

    def test_minion_tag_returns_false(self):
        # Only explicit 'minion' tag routes to cheap inference workers
        from devices.granny.daemon import _ticket_needs_cc

        assert _ticket_needs_cc({"worker": "igor", "tags": ["minion"]}) is False


def _make_bare_daemon(audit_passed=True, route_ok=True, inference_ok=True):
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
    audit.escalate_to_cc = (
        False  # HIGH-inertia=False by default; tests set True explicitly
    )
    audit.reasons = []

    device = MagicMock()
    device.intake_ticket.return_value = audit
    device.route_ticket.return_value = (route_ok, "cc")
    daemon._device = device
    daemon._inference_dispatch = MagicMock(return_value=inference_ok)
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
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 2
        # audit-passing tickets now go through inference_dispatch, not route_ticket
        assert daemon._inference_dispatch.call_count == 2

    def test_deduplicates_within_cycle(self):
        daemon = self._make_daemon()
        daemon._dispatched_ids = {"T-a"}
        tickets = [_ticket("T-a"), _ticket("T-b")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 1
        assert daemon._inference_dispatch.call_count == 1

    def test_dedup_blocks_immediate_re_dispatch(self):
        # |= accumulation: T-a dispatched in cycle 1 stays blocked for all
        # subsequent cycles in the same daemon run. It won't be re-dispatched
        # until the daemon restarts and dispatched_cycle.json is cleared.
        daemon = self._make_daemon()
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count1 = daemon.run_once()  # dispatches T-a → _dispatched_ids = {T-a}
            count2 = daemon.run_once()  # T-a in set → skip
            count3 = daemon.run_once()  # T-a still in set → skip (|= accumulates)

        assert count1 == 1
        assert count2 == 0
        assert count3 == 0  # stays blocked — |= never forgets within a run

    def test_non_cc_routes_to_inference(self):
        daemon = self._make_daemon()
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=False),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 1
        daemon._device.route_ticket.assert_not_called()
        call_args = daemon._inference_dispatch.call_args
        assert call_args.args[0] == tickets[0]
        assert "on_complete" in call_args.kwargs

    def test_skips_failed_audit_with_no_escalation(self):
        daemon = self._make_daemon(audit_passed=False)
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["size too large"]
        )
        tickets = [_ticket("T-a")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 0
        daemon._device.route_ticket.assert_not_called()

    def test_audit_fail_blocks_ticket_and_alerts_cc(self):
        """Audit-fail tickets are blocked + CC.0 alerted, not silently skipped."""
        daemon = self._make_daemon(audit_passed=False)
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["missing Completion criteria"]
        )
        held = []
        daemon._hold_for_audit_fail = lambda tid, reasons: held.append(tid)
        tickets = [_ticket("T-bad-desc")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 0
        assert held == ["T-bad-desc"]
        daemon._inference_dispatch.assert_not_called()

    def test_high_inertia_ticket_is_held_not_dispatched(self):
        """HIGH-inertia (escalate_to_cc=True) tickets are blocked for CC approval,
        never auto-dispatched to a new CC session."""
        daemon = self._make_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=True, reasons=["HIGH-inertia: shim.py"]
        )
        blocked = []
        daemon._hold_for_cc_approval = lambda tid, reasons: blocked.append(tid)
        tickets = [_ticket("T-high")]

        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            count = daemon.run_once()

        assert count == 1
        assert blocked == ["T-high"]
        daemon._inference_dispatch.assert_not_called()
        daemon._device.route_ticket.assert_not_called()


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
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            daemon.run_once()
        # append called for CC.0 alert + feeds/granny publish
        cc_calls = [c for c in daemon._imap.append.call_args_list if c[0][0] == "CC.0"]
        assert len(cc_calls) == 1
        envelope = cc_calls[0][0][1]
        assert envelope.payload["kind"] == "audit_fail"
        assert envelope.payload["ticket_id"] == "T-bad"

    def test_run_once_alerts_on_route_fail(self):
        # Audit-passing tickets now go through inference_dispatch (OR cascade).
        # A route_fail occurs when inference_dispatch returns False.
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=True, escalate_to_cc=False, reasons=[]
        )
        daemon._inference_dispatch = MagicMock(return_value=False)
        tickets = [_ticket("T-route-fail")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            daemon.run_once()
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
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
        ):
            daemon.run_once()
            daemon.run_once()
        # CC.0 alert is deduped — fires only once; feeds/granny publishes each cycle
        cc_calls = [c for c in daemon._imap.append.call_args_list if c[0][0] == "CC.0"]
        assert len(cc_calls) == 1


class TestAlertedIdsPersistence:
    def test_alert_persists_to_disk(self, tmp_path, monkeypatch):
        """_alert_cc saves alerted dedup key to disk after first alert."""
        import devices.granny.daemon as mod

        monkeypatch.setattr(mod, "_ALERTED_IDS_FILE", tmp_path / "alerted_ids.json")
        daemon = _make_bare_daemon()
        daemon._alert_cc("T-foo", "reason", "audit_fail")
        data = json.loads((tmp_path / "alerted_ids.json").read_text())
        assert "T-foo:audit_fail" in data["keys"]

    def test_alert_loads_from_disk_on_init(self, tmp_path, monkeypatch):
        """After restart, previously alerted keys are not re-sent."""
        import devices.granny.daemon as mod

        alerted_file = tmp_path / "alerted_ids.json"
        alerted_file.write_text(json.dumps({"keys": ["T-old:high_inertia"]}))
        monkeypatch.setattr(mod, "_ALERTED_IDS_FILE", alerted_file)

        loaded = mod._load_alerted_ids()
        assert "T-old:high_inertia" in loaded

    def test_alert_cc_skip_after_restart(self, tmp_path, monkeypatch):
        """Simulate restart: pre-populate disk, load into new daemon, verify dedup."""
        import devices.granny.daemon as mod

        alerted_file = tmp_path / "alerted_ids.json"
        alerted_file.write_text(json.dumps({"keys": ["T-sec:high_inertia"]}))
        monkeypatch.setattr(mod, "_ALERTED_IDS_FILE", alerted_file)

        daemon = _make_bare_daemon()
        daemon._alerted_ids = mod._load_alerted_ids()
        daemon._alert_cc("T-sec", "HIGH-inertia", "high_inertia")
        # IMAP should NOT be called — already alerted before restart
        daemon._imap.append.assert_not_called()

    def test_hold_for_cc_approval_channel_post_deduped(self, tmp_path, monkeypatch):
        """Channel post in _hold_for_cc_approval fires only once per ticket+kind."""
        import devices.granny.daemon as mod

        monkeypatch.setattr(mod, "_ALERTED_IDS_FILE", tmp_path / "alerted_ids.json")
        daemon = _make_bare_daemon()
        channel_posts = []
        daemon._post_channel = lambda msg: channel_posts.append(msg)
        daemon._publish_feed = MagicMock()

        with patch("subprocess.run"):
            daemon._hold_for_cc_approval("T-hi", "Security tag")
            daemon._hold_for_cc_approval("T-hi", "Security tag")  # second call

        hi_posts = [p for p in channel_posts if "T-hi" in p]
        assert len(hi_posts) == 1, "channel post must fire only once per ticket"

    def test_hold_for_audit_fail_channel_post_deduped(self, tmp_path, monkeypatch):
        """Channel post in _hold_for_audit_fail fires only once per ticket+kind."""
        import devices.granny.daemon as mod

        monkeypatch.setattr(mod, "_ALERTED_IDS_FILE", tmp_path / "alerted_ids.json")
        daemon = _make_bare_daemon()
        channel_posts = []
        daemon._post_channel = lambda msg: channel_posts.append(msg)
        daemon._publish_feed = MagicMock()

        with patch("subprocess.run"):
            daemon._hold_for_audit_fail("T-bad", ["missing section"])
            daemon._hold_for_audit_fail("T-bad", ["missing section"])  # second call

        bad_posts = [p for p in channel_posts if "T-bad" in p]
        assert len(bad_posts) == 1, "channel post must fire only once per ticket"


class TestCheckRateLimit:
    def _cache_text(self, five_pct=0.0, seven_pct=0.0):
        return json.dumps(
            {
                "usage": {
                    "five_hour": {"utilization": five_pct},
                    "seven_day": {"utilization": seven_pct},
                }
            }
        )

    def test_ok_when_both_below_threshold(self):
        from devices.granny.daemon import _check_rate_limit

        mock_path = MagicMock()
        mock_path.read_text.return_value = self._cache_text(50.0, 50.0)
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is True
        assert signal is None

    def test_5h_trip_returns_5h_signal(self):
        from devices.granny.daemon import _check_rate_limit

        mock_path = MagicMock()
        mock_path.read_text.return_value = self._cache_text(
            five_pct=95.0, seven_pct=10.0
        )
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is False
        assert signal == "5h"
        assert pct == 95.0

    def test_7d_trip_returns_7d_signal(self):
        from devices.granny.daemon import _check_rate_limit

        mock_path = MagicMock()
        mock_path.read_text.return_value = self._cache_text(
            five_pct=10.0, seven_pct=95.0
        )
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is False
        assert signal == "7d"
        assert pct == 95.0

    def test_5h_takes_precedence_when_both_trip(self):
        from devices.granny.daemon import _check_rate_limit

        mock_path = MagicMock()
        mock_path.read_text.return_value = self._cache_text(
            five_pct=95.0, seven_pct=95.0
        )
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is False
        assert signal == "5h"

    def test_missing_cache_returns_ok(self):
        from devices.granny.daemon import _check_rate_limit

        mock_path = MagicMock()
        mock_path.read_text.side_effect = FileNotFoundError
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is True

    def test_7d_none_in_cache_treated_as_zero(self):
        from devices.granny.daemon import _check_rate_limit

        cache = json.dumps(
            {"usage": {"five_hour": {"utilization": 10.0}, "seven_day": None}}
        )
        mock_path = MagicMock()
        mock_path.read_text.return_value = cache
        with patch("devices.granny.daemon._USAGE_CACHE", mock_path):
            ok, signal, pct = _check_rate_limit()
        assert ok is True


class TestRunOnceRateLimit:
    def test_5h_rate_limit_pauses_dispatch(self, caplog):
        import logging

        daemon = _make_bare_daemon()
        with (
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(False, "5h", 95.0),
            ),
            caplog.at_level(logging.WARNING, logger="devices.granny.daemon"),
        ):
            count = daemon.run_once()
        assert count == 0
        assert "5h" in caplog.text

    def test_7d_rate_limit_pauses_dispatch(self, caplog):
        import logging

        daemon = _make_bare_daemon()
        with (
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(False, "7d", 14.0),
            ),
            caplog.at_level(logging.WARNING, logger="devices.granny.daemon"),
        ):
            count = daemon.run_once()
        assert count == 0
        assert "7d" in caplog.text

    def test_no_rate_limit_proceeds_to_dispatch(self):
        daemon = _make_bare_daemon()
        tickets = [_ticket("T-a")]
        with (
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 21.0),
            ),
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()
        assert count == 1


class TestCC0InProgress:
    """_cc0_in_progress() returns True when a worker=claude/cc ticket is in_progress."""

    def test_returns_true_when_claude_ticket_in_progress(self):
        from devices.granny.daemon import _cc0_in_progress

        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor.__enter__ = lambda s: s
        cursor.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("psycopg2.connect", return_value=conn):
            assert _cc0_in_progress() is True

    def test_returns_false_when_no_in_progress(self):
        from devices.granny.daemon import _cc0_in_progress

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.__enter__ = lambda s: s
        cursor.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch("psycopg2.connect", return_value=conn):
            assert _cc0_in_progress() is False

    def test_returns_false_on_db_error(self):
        from devices.granny.daemon import _cc0_in_progress

        with patch("psycopg2.connect", side_effect=Exception("db down")):
            assert _cc0_in_progress() is False

    def test_two_tickets_dispatch_in_one_cycle(self):
        """OR cascade is synchronous — no slot cap, both tickets dispatch."""
        daemon = _make_bare_daemon()
        tickets = [_ticket("T-a"), _ticket("T-b")]
        with (
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            count = daemon.run_once()
        assert count == 2
