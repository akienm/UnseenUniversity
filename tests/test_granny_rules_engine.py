"""Tests for Granny rules-engine daemon (T-granny-rules-engine-rewrite)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devices.granny.daemon import match_rule, run_once, _default_config


# ── match_rule ────────────────────────────────────────────────────────────────


def _rules():
    return _default_config()["rules"]


class TestMatchRule:
    def test_high_inertia_tag_routes_to_cc(self):
        ticket = {"id": "T-1", "tags": ["Security"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_provenance_tag_routes_to_cc(self):
        ticket = {"id": "T-1", "tags": ["Provenance", "Platform"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_master_role_routes_to_cc(self):
        ticket = {"id": "T-1", "tags": ["Platform"], "role": "master"}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_guru_role_routes_to_akien(self):
        # guru = Akien; Granny posts NEEDS_AKIEN channel nudge, does not dispatch to CC/DS
        ticket = {"id": "T-1", "tags": [], "role": "guru"}
        assert match_rule(ticket, _rules()) == "akien"

    def test_master_role_does_not_route_to_akien(self):
        ticket = {"id": "T-1", "tags": [], "role": "master"}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_builder_role_routes_to_dicksimnel(self):
        ticket = {"id": "T-1", "tags": ["Platform"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "DickSimnel.0"

    def test_creator_role_routes_to_dicksimnel(self):
        ticket = {"id": "T-1", "tags": [], "role": "creator"}
        assert match_rule(ticket, _rules()) == "DickSimnel.0"

    def test_no_role_no_tags_defaults_to_cc(self):
        ticket = {"id": "T-1", "tags": [], "role": ""}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_high_inertia_beats_builder_role(self):
        # Security tag wins over builder role — inertia rule is first
        ticket = {"id": "T-1", "tags": ["Security"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "CC.0"

    def test_empty_rules_returns_cc_fallback(self):
        ticket = {"id": "T-1", "tags": [], "role": "builder"}
        assert match_rule(ticket, []) == "CC.0"

    def test_none_tags_handled(self):
        ticket = {"id": "T-1", "tags": None, "role": "builder"}
        assert match_rule(ticket, _rules()) == "DickSimnel.0"

    def test_missing_role_handled(self):
        ticket = {"id": "T-1"}
        assert match_rule(ticket, _rules()) == "CC.0"


# ── run_once ──────────────────────────────────────────────────────────────────


def _config():
    return _default_config()


class TestRunOnce:
    def test_skips_when_worker_unavailable(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=False):
                with patch("devices.granny.daemon._dispatch_bus") as mock_bus:
                    run_once(_config())
        mock_bus.assert_not_called()

    def test_skips_cc0_when_busy(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=True):
                    with patch("devices.granny.daemon._dispatch_bus") as mock_bus:
                        run_once(_config())
        mock_bus.assert_not_called()

    def test_dispatches_to_cc0_via_bus(self):
        ticket = {"id": "T-cc", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fix it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        mock_bus.assert_called_once()
        # Third positional arg is worker_mailbox — default config has CC.0 mailbox "cc.0"
        assert mock_bus.call_args[0][2] == "cc.0"

    def test_dispatches_to_dicksimnel_via_bus(self):
        ticket = {"id": "T-ds", "tags": [], "role": "builder", "status": "sprint",
                  "title": "Build it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        mock_bus.assert_called_once()
        # Third positional arg is worker_mailbox
        assert mock_bus.call_args[0][2] == "dicksimnel.0"

    def test_dispatch_failure_does_not_raise(self):
        ticket = {"id": "T-fail", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fail"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0):
            run_once(_config(), imap=imap)  # must not raise

    def test_guru_ticket_dispatches_to_akien_not_cc_or_ds(self):
        ticket = {"id": "T-guru", "tags": [], "role": "guru", "status": "sprint",
                  "title": "Needs Akien"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.daemon._dispatch_akien", return_value=True) as mock_akien:
                with patch("devices.granny.daemon._dispatch_bus") as mock_bus:
                    with patch("devices.granny.daemon._dispatch_dicksimnel") as mock_ds:
                        with patch("devices.granny.daemon._post_channel"):
                            run_once(_config())
        mock_akien.assert_called_once()
        mock_bus.assert_not_called()
        mock_ds.assert_not_called()

    def test_guru_ticket_skips_availability_check(self):
        ticket = {"id": "T-guru2", "tags": [], "role": "guru", "status": "sprint",
                  "title": "Human needed"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=False) as mock_avail:
                with patch("devices.granny.daemon._dispatch_akien", return_value=True):
                    with patch("devices.granny.daemon._post_channel"):
                        run_once(_config())
        mock_avail.assert_not_called()

    def test_dispatch_cc0_calls_bus_dispatch(self):
        ticket = {"id": "T-cc-mark", "tags": [], "role": "master", "status": "sprint",
                  "title": "Mark it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        mock_bus.assert_called_once()
        assert mock_bus.call_args[0][2] == "cc.0", "CC.0 must dispatch to cc.0 mailbox"

    def test_one_at_a_time_prevents_second_dispatch_same_cycle(self):
        tickets = [
            {"id": "T-first", "tags": [], "role": "master", "status": "sprint", "title": "First"},
            {"id": "T-second", "tags": [], "role": "master", "status": "sprint", "title": "Second"},
        ]
        dispatched_ids = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox):
            dispatched_ids.append(ticket["id"])
            return True

        with patch("devices.granny.daemon._sprint_tickets", return_value=tickets), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        assert dispatched_ids == ["T-first"], "second CC ticket must be deferred to next cycle"

    def test_high_inertia_ticket_routes_to_cc_not_dicksimnel(self):
        ticket = {"id": "T-sec", "tags": ["Security"], "role": "builder",
                  "status": "sprint", "title": "Secure it"}
        dispatched_mailboxes = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox):
            dispatched_mailboxes.append(worker_mailbox)
            return True

        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        assert dispatched_mailboxes == ["cc.0"], f"Security tag must route to cc.0, got {dispatched_mailboxes}"

    def test_escalated_ticket_routes_to_cc_not_dicksimnel(self):
        """Escalated tickets bypass DickSimnel and go directly to CC."""
        ticket = {"id": "T-esc", "tags": [], "role": "builder", "status": "escalated",
                  "title": "DickSimnel failed this"}
        dispatched_mailboxes = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox):
            dispatched_mailboxes.append(worker_mailbox)
            return True

        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("devices.granny.daemon._dispatch_dicksimnel") as mock_ds, \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        assert dispatched_mailboxes == ["cc.0"], "escalated tickets must go to CC (cc.0)"
        mock_ds.assert_not_called()
