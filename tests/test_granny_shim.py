"""Tests for GrannyShim and GrannyWeatherwaxDevice channel/audit behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devices.granny.device import GrannyWeatherwaxDevice, _audit_ticket
from devices.granny.shim import GrannyShim, _BACKOFF_INITIAL_SEC

# ── GrannyShim ────────────────────────────────────────────────────────────────


class TestGrannyShim:
    def test_self_test_passes(self):
        shim = GrannyShim()
        result = shim.self_test()
        assert result["passed"] is True

    def test_device_id(self):
        assert GrannyShim().device_id == "granny-weatherwax"

    def test_intake_returns_expected_shape(self):
        shim = GrannyShim()
        result = shim.intake_ticket(
            {
                "id": "T-test",
                "title": "test ticket",
                "size": "S",
                "description": (
                    "**Affected files:** none\n"
                    "**Scope boundary:** test only\n"
                    "**Completion criteria:** none needed"
                ),
            }
        )
        assert "passed" in result
        assert "reasons" in result
        assert "escalate_to_cc" in result
        assert isinstance(result["reasons"], list)

    def test_edge_weights_returns_list(self):
        shim = GrannyShim()
        weights = shim.edge_weights("Cognition")
        assert isinstance(weights, list)
        if weights:
            assert "worker_id" in weights[0]
            assert "weight" in weights[0]

    def test_health_returns_status(self):
        shim = GrannyShim()
        h = shim.health()
        assert h["status"] in ("healthy", "degraded")


# ── Channel messages are human-readable ──────────────────────────────────────


class TestGrannyChannelMessages:
    def _make_granny(self):
        g = GrannyWeatherwaxDevice()
        g._posts = []

        def fake_post(channel, message):
            g._posts.append((channel, message))

        g._post_to_channel = fake_post
        return g

    def test_audit_fail_message_is_human_readable(self):
        g = self._make_granny()
        g.intake_ticket({"id": "T-bad", "title": "", "size": "X", "description": ""})
        assert g._posts, "expected a channel post on audit failure"
        _, msg = g._posts[0]
        assert "T-bad" in msg
        assert "GRANNY_AUDIT_FAIL" not in msg  # old machine code must be gone

    def test_route_message_is_human_readable(self):
        g = self._make_granny()
        g.route_ticket(
            {
                "id": "T-cog-1",
                "title": "fix the thalamus",
                "size": "S",
                "tags": ["Cognition"],
                "description": "fix it",
            }
        )
        assert g._posts, "expected a channel post on route"
        _, msg = g._posts[0]
        assert "T-cog-1" in msg
        assert "igor" in msg  # routed to igor per default routing
        assert "GRANNY_ROUTE" not in msg  # old machine code must be gone

    def test_escalate_message_is_human_readable(self):
        g = self._make_granny()
        g.escalate_to_cc({"id": "T-arch", "title": "big arch change"}, "no route found")
        assert g._posts
        _, msg = g._posts[0]
        assert "T-arch" in msg
        assert "CC" in msg
        assert "GRANNY_ESCALATE" not in msg

    def test_high_inertia_escalates_with_readable_message(self):
        g = self._make_granny()
        g.intake_ticket(
            {
                "id": "T-sec",
                "title": "security change",
                "size": "M",
                "tags": ["Security"],
                "description": "**Affected files:** x\n**Scope boundary:** y\n**Completion criteria:** z",
            }
        )
        assert g._posts
        _, msg = g._posts[0]
        assert "T-sec" in msg
        assert "CC" in msg or "escalat" in msg.lower()


# ── _post_to_channel uses new utility ────────────────────────────────────────


class TestGrannyChannelPostsViaNewUtility:
    def test_uses_unseen_university_channel(self):
        g = GrannyWeatherwaxDevice()
        with patch("unseen_university.channel.post_to_channel") as mock_post:
            g._post_to_channel("shared", "hello from granny")

        mock_post.assert_called_once_with(
            "hello from granny", author="granny-weatherwax", channel="shared"
        )


# ── GrannyShim daemon lifecycle ───────────────────────────────────────────────


class TestGrannyShimDaemonLifecycle:
    def test_start_calls_daemon_start(self):
        """Criterion 1: GrannyShim.start() starts the daemon."""
        shim = GrannyShim()
        with patch("devices.granny.daemon.get_daemon") as mock_get:
            mock_daemon = MagicMock()
            mock_get.return_value = mock_daemon
            shim.start()
        mock_daemon.start.assert_called_once()

    def test_stop_calls_daemon_stop(self):
        """Criterion 3: GrannyShim.stop() stops the daemon cleanly."""
        shim = GrannyShim()
        with patch("devices.granny.daemon.get_daemon") as mock_get:
            mock_daemon = MagicMock()
            mock_get.return_value = mock_daemon
            shim.stop()
        mock_daemon.stop.assert_called_once()

    def test_daemon_is_running_after_shim_start(self):
        """Criteria 1+2: daemon thread is alive immediately after shim starts."""
        import devices.granny.daemon as daemon_mod

        # Reset singleton so this test gets a clean daemon
        daemon_mod._daemon = None
        shim = GrannyShim()
        try:
            shim.start()
            assert daemon_mod.get_daemon().is_running()
        finally:
            shim.stop()
            daemon_mod._daemon = None


# ── ensure_daemon_running watchdog hook ───────────────────────────────────────


class TestEnsureDaemonRunning:
    def test_relaunches_stopped_daemon(self, caplog):
        import logging

        shim = GrannyShim()
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = False

        with (
            patch("devices.granny.daemon.get_daemon", return_value=mock_daemon),
            caplog.at_level(logging.INFO, logger="devices.granny.shim"),
        ):
            result = shim.ensure_daemon_running()

        assert result is True
        mock_daemon.start.assert_called_once()
        assert any("relaunched granny daemon" in r.message for r in caplog.records)

    def test_no_relaunch_when_running(self):
        shim = GrannyShim()
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = True

        with patch("devices.granny.daemon.get_daemon", return_value=mock_daemon):
            result = shim.ensure_daemon_running()

        assert result is True
        mock_daemon.start.assert_not_called()

    def test_returns_false_and_backs_off_on_relaunch_failure(self):
        shim = GrannyShim()
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = False
        mock_daemon.start.side_effect = Exception("IMAP down")

        with patch("devices.granny.daemon.get_daemon", return_value=mock_daemon):
            initial_backoff = shim._backoff_sec
            result = shim.ensure_daemon_running()

        assert result is False
        assert shim._backoff_sec > initial_backoff

    def test_relaunch_count_increments(self):
        shim = GrannyShim()
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = False

        with patch("devices.granny.daemon.get_daemon", return_value=mock_daemon):
            shim.ensure_daemon_running()
            shim.ensure_daemon_running()

        assert shim._relaunch_count == 2

    def test_backoff_resets_after_healthy_check(self):
        shim = GrannyShim()
        shim._backoff_sec = 60.0
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = True

        with patch("devices.granny.daemon.get_daemon", return_value=mock_daemon):
            shim.ensure_daemon_running()

        assert shim._backoff_sec == _BACKOFF_INITIAL_SEC
