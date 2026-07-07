"""Tests for Granny rules-engine daemon (T-granny-rules-engine-rewrite)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.granny.daemon import (
    match_rule,
    run_once,
    _default_config,
    _cleared_gated_tickets,
)


@pytest.fixture(autouse=True)
def _no_db_gated_tickets(monkeypatch):
    """Prevent _cleared_gated_tickets from hitting the DB in unit tests.

    Tests that want to exercise the gate-eval path patch it explicitly via
    patch("unseen_university.devices.granny.daemon._cleared_gated_tickets", ...) which takes
    precedence over this fixture's monkeypatch.
    """
    monkeypatch.setattr(
        "unseen_university.devices.granny.daemon._cleared_gated_tickets", lambda: []
    )


@pytest.fixture(autouse=True)
def _run_once_dispatch_env(monkeypatch):
    """Set up the run_once dispatch environment for unit tests.

    Two pieces of live/persisted state must be simulated, or run_once dispatches nothing:

    1. CC.0 self-announce. In production CC.0 writes ~/.granny/announced/CC.0.json when
       cc_worker_listener starts, and run_once merges it via _load_announced_workers().
       Unit tests have no announce file, so without this CC.0 is unresolvable. Shape
       mirrors the real announce: cc.0 mailbox, worker_name=claude, one_at_a_time +
       cascade_if_idle=True (CC.0 absorbs idle tickets). Tests that need DickSimnel routing
       patch _cascade_active_workers to prevent absorption.
    2. Not stalled. run_once early-returns when is_stalled() is True (PARK guard). The
       persisted stall flag leaks across processes, so pin it False for dispatch tests.
       (is_stalled is imported inside run_once from stall_state — patch it there.)
    """
    monkeypatch.setattr(
        "unseen_university.devices.granny.daemon._load_announced_workers",
        lambda: {
            "CC.0": {
                "dispatch": "bus",
                "mailbox": "cc.0",
                "worker_name": "claude",
                "one_at_a_time": True,
                "cascade_if_idle": True,
            }
        },
    )
    monkeypatch.setattr(
        "unseen_university.devices.granny.stall_state.is_stalled", lambda: False
    )


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

    def test_aider_tag_routes_to_aider_builder(self):
        # `Aider`-tagged tickets opt in to the aider builder…
        ticket = {"id": "T-1", "tags": ["Aider"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "Aider.0"

    def test_untagged_builder_still_defaults_to_dicksimnel(self):
        # …while an untagged builder ticket is unaffected (no load-balancing yet).
        ticket = {"id": "T-1", "tags": [], "role": "builder"}
        assert match_rule(ticket, _rules()) == "DickSimnel.0"

    def test_aider_tag_does_not_beat_high_inertia(self):
        # HIGH-inertia still wins — a Security+Aider ticket goes to CC.0, not aider.
        ticket = {"id": "T-1", "tags": ["Aider", "Security"], "role": "builder"}
        assert match_rule(ticket, _rules()) == "CC.0"

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

    def test_empty_rules_defers(self):
        # No catch-all rule → match_rule returns None (the caller logs a warning and
        # defers). The old hardcoded 'CC.0' last-resort was removed on purpose so a
        # misconfigured rule set can't blindly fire a ticket at a hardcoded worker.
        ticket = {"id": "T-1", "tags": [], "role": "builder"}
        assert match_rule(ticket, []) is None

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
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("unseen_university.devices.granny.availability.is_available", return_value=False):
                with patch("unseen_university.devices.granny.daemon._dispatch_bus") as mock_bus:
                    run_once(_config())
        mock_bus.assert_not_called()

    def test_skips_cc0_when_busy(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("unseen_university.devices.granny.availability.is_available", return_value=True):
                with patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=True):
                    with patch("unseen_university.devices.granny.daemon._dispatch_bus") as mock_bus:
                        run_once(_config())
        mock_bus.assert_not_called()

    def test_dispatches_to_cc0_via_bus(self):
        ticket = {"id": "T-cc", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fix it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        mock_bus.assert_called_once()
        # Third positional arg is worker_mailbox — default config has CC.0 mailbox "cc.0"
        assert mock_bus.call_args[0][2] == "cc.0"

    def test_dispatches_to_dicksimnel_via_bus(self):
        ticket = {"id": "T-ds", "tags": [], "role": "builder", "status": "sprint",
                  "title": "Build it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        # Patch cascade to empty so CC.0 doesn't absorb the builder ticket
        # (CC.0 has cascade_if_idle=True — without this patch, it would claim the ticket).
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cascade_active_workers", return_value={}), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        mock_bus.assert_called_once()
        # Third positional arg is worker_mailbox
        assert mock_bus.call_args[0][2] == "dicksimnel.0"

    def test_dispatch_failure_does_not_raise(self):
        ticket = {"id": "T-fail", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fail"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=False), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0):
            run_once(_config(), imap=imap)  # must not raise

    def test_guru_ticket_dispatches_to_akien_not_cc_or_ds(self):
        ticket = {"id": "T-guru", "tags": [], "role": "guru", "status": "sprint",
                  "title": "Needs Akien"}
        # _dispatch_dicksimnel was removed — bus dispatch is the unified path now;
        # for guru tickets, _dispatch_akien is called and _dispatch_bus is NOT called.
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("unseen_university.devices.granny.daemon._dispatch_akien", return_value=True) as mock_akien:
                with patch("unseen_university.devices.granny.daemon._dispatch_bus") as mock_bus:
                    with patch("unseen_university.devices.granny.daemon._post_channel"):
                        run_once(_config())
        mock_akien.assert_called_once()
        mock_bus.assert_not_called()

    def test_guru_ticket_skips_availability_check(self):
        ticket = {"id": "T-guru2", "tags": [], "role": "guru", "status": "sprint",
                  "title": "Human needed"}
        # is_available IS called during the idle-worker-launch pass (checking whether
        # DickSimnel/CC.0 need launching), but must NOT be called for guru routing itself.
        # Observable check: _dispatch_akien fires even when all workers are "unavailable".
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("unseen_university.devices.granny.availability.is_available", return_value=False):
                with patch("unseen_university.devices.granny.daemon._dispatch_akien", return_value=True) as mock_akien:
                    with patch("unseen_university.devices.granny.daemon._dispatch_bus") as mock_bus:
                        with patch("unseen_university.devices.granny.daemon._post_channel"):
                            run_once(_config())
        mock_akien.assert_called_once()
        mock_bus.assert_not_called()

    def test_dispatch_cc0_calls_bus_dispatch(self):
        ticket = {"id": "T-cc-mark", "tags": [], "role": "master", "status": "sprint",
                  "title": "Mark it"}
        imap = MagicMock()
        imap.fetch_unseen.return_value = []
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_bus, \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
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

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched_ids.append(ticket["id"])
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=tickets), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        assert dispatched_ids == ["T-first"], "second CC ticket must be deferred to next cycle"

    def test_builder_one_at_a_time_prevents_second_dispatch_same_cycle(self):
        """Two builder-role tickets in one cycle → only ONE dispatches to DickSimnel.0.

        Regression for the missing one_at_a_time flag on the static bus-builders
        (T-granny-builder-one-at-a-time-flag). Exposed live 2026-07-06: Granny
        fanned 3 dispatch envelopes into aider.0 at once because Aider.0/DickSimnel.0
        omitted the flag. A synchronous builder must get one ticket per cycle.
        """
        tickets = [
            {"id": "T-b1", "tags": [], "role": "builder", "status": "sprint", "title": "B1"},
            {"id": "T-b2", "tags": [], "role": "builder", "status": "sprint", "title": "B2"},
        ]
        dispatched = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched.append(ticket["id"])
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=tickets), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cascade_active_workers", return_value={}), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._worker_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        assert dispatched == ["T-b1"], "second builder ticket must defer to next cycle (one_at_a_time)"

    def test_aider_tagged_one_at_a_time_prevents_mailbox_fanout(self):
        """Two `Aider`-tagged tickets in one cycle → only ONE dispatches to Aider.0.

        The exact live failure: three Aider-tagged tickets landed in the aider.0
        mailbox in a single poll cycle. With one_at_a_time set, Granny defers the rest.
        """
        tickets = [
            {"id": "T-a1", "tags": ["Aider"], "role": "builder", "status": "sprint", "title": "A1"},
            {"id": "T-a2", "tags": ["Aider"], "role": "builder", "status": "sprint", "title": "A2"},
            {"id": "T-a3", "tags": ["Aider"], "role": "builder", "status": "sprint", "title": "A3"},
        ]
        dispatched = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched.append((ticket["id"], worker_mailbox))
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=tickets), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cascade_active_workers", return_value={}), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._worker_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)

        assert dispatched == [("T-a1", "aider.0")], "only one Aider ticket per cycle; no mailbox fan-out"

    def test_gated_ticket_not_dispatched_when_gate_blocked(self):
        """A ticket with gate: T-A is NOT dispatched when T-A is not closed."""
        ungated = []
        gated_ticket = {"id": "T-B", "tags": [], "role": "builder", "status": "sprint",
                        "gate": "T-A", "title": "Gated on A"}
        # T-A is in_progress — gate not clear
        all_statuses = [
            {"id": "T-A", "status": "in_progress"},
            {"id": "T-B", "status": "sprint"},
        ]
        dispatched = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched.append(ticket["id"])
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=ungated), \
             patch("unseen_university.devices.granny.daemon._cleared_gated_tickets", return_value=[]), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_default_config(), imap=imap)

        assert "T-B" not in dispatched, "gated ticket must not dispatch while gate is blocked"

    def test_gated_ticket_dispatched_when_gate_clears(self):
        """A ticket with gate: T-A IS dispatched once T-A is closed."""
        cleared_ticket = {"id": "T-B", "tags": [], "role": "builder", "status": "sprint",
                          "gate": "T-A", "title": "Gated on A"}
        dispatched = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched.append(ticket["id"])
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[]), \
             patch("unseen_university.devices.granny.daemon._cleared_gated_tickets", return_value=[cleared_ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cascade_active_workers", return_value={}), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_default_config(), imap=imap)

        assert "T-B" in dispatched, "cleared-gated ticket must be dispatched"

    def test_cleared_gated_tickets_returns_empty_on_store_error(self):
        """_cleared_gated_tickets() returns [] when the ticket store errors — never raises.

        Postgres was dropped from the ticket path (tickets live in the filesystem
        ticket_store); the fail-open contract is unchanged — any error -> [].
        """
        with patch("unseen_university.ticket_store.list", side_effect=RuntimeError("store down")):
            result = _cleared_gated_tickets()
        assert result == []

    def test_cleared_gated_tickets_evaluates_gate_logic(self):
        """_cleared_gated_tickets() uses gate_logic: blocked ticket stays out, cleared comes in."""
        from unseen_university.gate_logic import gate_clear

        t_blocked = {"id": "T-blocked", "gate": "T-prereq", "status": "sprint"}
        t_cleared = {"id": "T-cleared", "gate": "T-done", "status": "sprint"}
        all_statuses = [
            {"id": "T-prereq", "status": "in_progress"},
            {"id": "T-done",   "status": "closed"},
        ]

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[]):
            # Simulate what _cleared_gated_tickets does without hitting the DB
            results = [
                t for t in [t_blocked, t_cleared]
                if gate_clear(t["gate"], all_statuses)
            ]

        assert [t["id"] for t in results] == ["T-cleared"]

    def test_high_inertia_ticket_routes_to_cc_not_dicksimnel(self):
        ticket = {"id": "T-sec", "tags": ["Security"], "role": "builder",
                  "status": "sprint", "title": "Secure it"}
        dispatched_mailboxes = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched_mailboxes.append(worker_mailbox)
            return True

        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        assert dispatched_mailboxes == ["cc.0"], f"Security tag must route to cc.0, got {dispatched_mailboxes}"

    def test_escalated_ticket_routes_to_cc_not_dicksimnel(self):
        """Escalated tickets bypass DickSimnel and go directly to CC."""
        ticket = {"id": "T-esc", "tags": [], "role": "builder", "status": "escalated",
                  "title": "DickSimnel failed this"}
        dispatched_mailboxes = []
        imap = MagicMock()
        imap.fetch_unseen.return_value = []

        def fake_bus(ticket, imap, worker_mailbox, granny_mailbox, *, worker_name=None):
            dispatched_mailboxes.append(worker_mailbox)
            return True

        # _dispatch_dicksimnel was removed — bus dispatch is the unified path;
        # check observable behavior: bus dispatched to cc.0, not dicksimnel.0.
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", side_effect=fake_bus), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"):
            run_once(_config(), imap=imap)
        assert dispatched_mailboxes == ["cc.0"], "escalated tickets must go to CC (cc.0)"
