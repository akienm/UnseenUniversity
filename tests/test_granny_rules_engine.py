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
                with patch("devices.granny.daemon._dispatch_cc0") as mock_cc:
                    run_once(_config())
        mock_cc.assert_not_called()

    def test_skips_cc0_when_busy(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=True):
                    with patch("devices.granny.daemon._dispatch_cc0") as mock_cc:
                        run_once(_config())
        mock_cc.assert_not_called()

    def test_dispatches_to_cc0_via_send_keys(self):
        ticket = {"id": "T-cc", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fix it"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", return_value=True) as mock_cc:
                        with patch("devices.granny.daemon._post_channel"):
                            run_once(_config())
        mock_cc.assert_called_once()

    def test_dispatches_to_dicksimnel_via_set_worker(self):
        ticket = {"id": "T-ds", "tags": [], "role": "builder", "status": "sprint",
                  "title": "Build it"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._dispatch_dicksimnel", return_value=True) as mock_ds:
                    with patch("devices.granny.daemon._post_channel"):
                        run_once(_config())
        mock_ds.assert_called_once()

    def test_dispatch_failure_does_not_raise(self):
        ticket = {"id": "T-fail", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fail"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", return_value=False):
                        run_once(_config())  # must not raise

    def test_guru_ticket_dispatches_to_akien_not_cc_or_ds(self):
        ticket = {"id": "T-guru", "tags": [], "role": "guru", "status": "sprint",
                  "title": "Needs Akien"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.daemon._dispatch_akien", return_value=True) as mock_akien:
                with patch("devices.granny.daemon._dispatch_cc0") as mock_cc:
                    with patch("devices.granny.daemon._dispatch_dicksimnel") as mock_ds:
                        with patch("devices.granny.daemon._post_channel"):
                            run_once(_config())
        mock_akien.assert_called_once()
        mock_cc.assert_not_called()
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

    def test_dispatch_cc0_marks_ticket_in_progress(self):
        ticket = {"id": "T-cc-mark", "tags": [], "role": "master", "status": "sprint",
                  "title": "Mark it"}
        run_calls = []

        def fake_run(cmd, **kwargs):
            run_calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]),
            patch("devices.granny.availability.is_available", return_value=True),
            patch("devices.granny.daemon._cc0_busy", return_value=False),
            patch("subprocess.run", side_effect=fake_run),
            patch("devices.granny.daemon._post_channel"),
        ):
            run_once(_config())

        setstatus_calls = [c for c in run_calls if "setstatus" in str(c)]
        assert setstatus_calls, "setstatus in_progress must be called after send-keys dispatch"
        assert any("in_progress" in str(c) for c in setstatus_calls)

    def test_one_at_a_time_prevents_second_dispatch_same_cycle(self):
        tickets = [
            {"id": "T-first", "tags": [], "role": "master", "status": "sprint", "title": "First"},
            {"id": "T-second", "tags": [], "role": "master", "status": "sprint", "title": "Second"},
        ]
        dispatched_ids = []

        def fake_cc(ticket, session="claude-main"):
            dispatched_ids.append(ticket["id"])
            return True

        with patch("devices.granny.daemon._sprint_tickets", return_value=tickets):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", side_effect=fake_cc):
                        with patch("devices.granny.daemon._post_channel"):
                            run_once(_config())

        assert dispatched_ids == ["T-first"], "second CC ticket must be deferred to next cycle"

    def test_high_inertia_ticket_routes_to_cc_not_dicksimnel(self):
        ticket = {"id": "T-sec", "tags": ["Security"], "role": "builder",
                  "status": "sprint", "title": "Secure it"}
        dispatched_to = []
        def fake_cc(ticket, session="claude-main"):
            dispatched_to.append("cc0")
            return True
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", side_effect=fake_cc):
                        with patch("devices.granny.daemon._post_channel"):
                            run_once(_config())
        assert dispatched_to == ["cc0"]

    def test_escalated_ticket_routes_to_cc_not_dicksimnel(self):
        """Escalated tickets bypass DickSimnel and go directly to CC."""
        ticket = {"id": "T-esc", "tags": [], "role": "builder", "status": "escalated",
                  "title": "DickSimnel failed this"}
        dispatched_to = []
        def fake_cc(ticket, session="claude-main"):
            dispatched_to.append("cc0")
            return True
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", side_effect=fake_cc):
                        with patch("devices.granny.daemon._dispatch_dicksimnel") as mock_ds:
                            with patch("devices.granny.daemon._post_channel"):
                                run_once(_config())
        assert dispatched_to == ["cc0"], "escalated tickets must go to CC"
        mock_ds.assert_not_called()
