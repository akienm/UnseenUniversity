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

    def test_guru_role_routes_to_cc(self):
        ticket = {"id": "T-1", "tags": [], "role": "guru"}
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
    def _mock_available(self, available_workers: set):
        return lambda wid: wid in available_workers

    def test_skips_already_dispatched(self):
        ticket = {"id": "T-old", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                result = run_once(_config(), {"T-old"})
        assert "T-old" in result  # still in dispatched, not re-dispatched

    def test_skips_when_worker_unavailable(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=False):
                result = run_once(_config(), set())
        assert "T-new" not in result

    def test_skips_cc0_when_busy(self):
        ticket = {"id": "T-new", "tags": [], "role": "master", "status": "sprint"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=True):
                    result = run_once(_config(), set())
        assert "T-new" not in result

    def test_dispatches_to_cc0_via_send_keys(self):
        ticket = {"id": "T-cc", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fix it"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", return_value=True) as mock_cc:
                        with patch("devices.granny.daemon._post_channel"):
                            result = run_once(_config(), set())
        assert "T-cc" in result
        mock_cc.assert_called_once()

    def test_dispatches_to_dicksimnel_via_set_worker(self):
        ticket = {"id": "T-ds", "tags": [], "role": "builder", "status": "sprint",
                  "title": "Build it"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._dispatch_dicksimnel", return_value=True) as mock_ds:
                    with patch("devices.granny.daemon._post_channel"):
                        result = run_once(_config(), set())
        assert "T-ds" in result
        mock_ds.assert_called_once()

    def test_does_not_add_on_dispatch_failure(self):
        ticket = {"id": "T-fail", "tags": [], "role": "master", "status": "sprint",
                  "title": "Fail"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]):
            with patch("devices.granny.availability.is_available", return_value=True):
                with patch("devices.granny.daemon._cc0_busy", return_value=False):
                    with patch("devices.granny.daemon._dispatch_cc0", return_value=False):
                        result = run_once(_config(), set())
        assert "T-fail" not in result

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
                            run_once(_config(), set())
        assert dispatched_to == ["cc0"]
