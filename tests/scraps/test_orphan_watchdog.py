"""Tests for devices.scraps.jobs.orphan_watchdog.OrphanWatchdog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest


def _ticket(
    id="T-test",
    size="S",
    status="in_progress",
    age_minutes=180,
) -> dict:
    """Build a minimal in_progress ticket dict."""
    claimed_at = (
        datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    ).isoformat()
    return {
        "id": id,
        "title": f"ticket {id}",
        "size": size,
        "status": status,
        "claimed_at": claimed_at,
    }


def _make_watchdog(**kwargs):
    from devices.scraps.jobs.orphan_watchdog import OrphanWatchdog

    return OrphanWatchdog(**kwargs)


class TestOrphanWatchdogTimeouts:
    def test_s_ticket_not_reset_below_120m(self):
        wd = _make_watchdog()
        t = _ticket(size="S", age_minutes=90)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket") as mock_reset,
            patch.object(wd, "_post_channel"),
        ):
            wd.run()
        mock_reset.assert_not_called()

    def test_s_ticket_reset_at_120m(self):
        wd = _make_watchdog()
        t = _ticket(id="T-stale", size="S", age_minutes=121)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=True) as mock_reset,
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        mock_reset.assert_called_once_with("T-stale")
        assert result == ["T-stale"]

    def test_m_ticket_timeout_is_240m(self):
        wd = _make_watchdog()
        t_young = _ticket(id="T-young", size="M", age_minutes=200)
        t_old = _ticket(id="T-old", size="M", age_minutes=250)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t_young, t_old]),
            patch.object(wd, "_reset_ticket", return_value=True) as mock_reset,
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        mock_reset.assert_called_once_with("T-old")
        assert result == ["T-old"]

    def test_l_ticket_timeout_is_360m(self):
        wd = _make_watchdog()
        t = _ticket(size="L", age_minutes=361)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=True),
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        assert len(result) == 1

    def test_unknown_size_uses_default_240m(self):
        wd = _make_watchdog()
        t = _ticket(size="?", age_minutes=241)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=True),
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        assert len(result) == 1

    def test_override_timeout_respected(self):
        wd = _make_watchdog(timeout_overrides={"S": 30})
        t = _ticket(size="S", age_minutes=35)
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=True),
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        assert len(result) == 1


class TestOrphanWatchdogChannelPost:
    def test_granny_orphan_reset_posted(self):
        wd = _make_watchdog()
        t = _ticket(id="T-zombie", size="S", age_minutes=130)
        posted = []
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=True),
            patch.object(wd, "_post_channel", side_effect=posted.append),
        ):
            wd.run()
        assert len(posted) == 1
        assert "GRANNY_ORPHAN_RESET" in posted[0]
        assert "T-zombie" in posted[0]
        assert "reason=timeout" in posted[0]

    def test_no_post_when_reset_fails(self):
        wd = _make_watchdog()
        t = _ticket(size="S", age_minutes=130)
        posted = []
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket", return_value=False),
            patch.object(wd, "_post_channel", side_effect=posted.append),
        ):
            wd.run()
        assert len(posted) == 0

    def test_no_action_when_queue_empty(self):
        wd = _make_watchdog()
        with (
            patch.object(wd, "_load_in_progress", return_value=[]),
            patch.object(wd, "_reset_ticket") as mock_reset,
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        mock_reset.assert_not_called()
        assert result == []


class TestOrphanWatchdogEdgeCases:
    def test_missing_claimed_at_skipped(self):
        wd = _make_watchdog()
        t = {"id": "T-no-ts", "size": "S", "status": "in_progress"}
        with (
            patch.object(wd, "_load_in_progress", return_value=[t]),
            patch.object(wd, "_reset_ticket") as mock_reset,
            patch.object(wd, "_post_channel"),
        ):
            result = wd.run()
        mock_reset.assert_not_called()
        assert result == []

    def test_db_error_returns_empty(self):
        wd = _make_watchdog()
        with patch.object(wd, "_load_in_progress", return_value=[]):
            result = wd.run()
        assert result == []


# Note: GrannyDaemon class was removed in the Granny rules-engine rewrite.
# The orphan watchdog is now a standalone scraps job, not integrated into Granny.
# Tests for standalone OrphanWatchdog.run() are in the classes above.
