"""
tests/granny/test_listen_notify.py — _wait_for_work polling-wakeup unit test.

The Postgres LISTEN/NOTIFY wakeup (_setup_listen_notify / _wait_for_notify) was
removed by T-ticket-pg-drop (#5, D-build-queue-filesystem-first): the daemon
reads ticket state from the filesystem store, so the wakeup is pure interval
polling. Instant FS-signal wake is a follow-up (T-granny-fs-wake-signal).

Covers:
- _wait_for_work(t) sleeps for t seconds (the poll interval) — no Postgres.
"""

from __future__ import annotations

from unittest.mock import patch


def test_wait_for_work_sleeps_for_interval():
    with patch("devices.granny.daemon.time.sleep") as mock_sleep:
        from devices.granny.daemon import _wait_for_work
        _wait_for_work(30)
        mock_sleep.assert_called_once_with(30)
