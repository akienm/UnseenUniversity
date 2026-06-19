"""Tests for Gap A: dispatch timeout resets to sprint + builder cooldown.

Reads migrated to the filesystem ticket store (D-build-queue-filesystem-first):
``_escalate_stale_dispatched`` now reads ``ticket_store.list(status_filter=
'dispatched')`` and ages each ticket via ``_is_stale`` on ``body.updated_at``,
so the fixtures stub ``ticket_store.list`` with old-stamped dispatched tickets.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _stale_ticket(tid, worker):
    """A dispatched ticket whose updated_at is well past the ack timeout."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=10_000)).isoformat()
    return {"id": tid, "worker": worker, "status": "dispatched", "updated_at": old}


def _patch_store(stale_tickets):
    """Patch the filesystem ticket-store list() the reader now consumes."""
    return patch("unseen_university.ticket_store.list", return_value=stale_tickets)


def test_timeout_resets_to_sprint_not_escalated(tmp_path):
    """A timed-out dispatched ticket must go to 'sprint', not 'escalated'."""
    with (
        _patch_store([_stale_ticket("T-timeout-1", "dicksimnel")]),
        patch("devices.granny.availability._AVAILABLE_DIR", tmp_path),
        patch("devices.granny.daemon._setstatus_direct") as mock_status,
    ):
        mock_status.return_value = True
        from devices.granny.daemon import _escalate_stale_dispatched
        count = _escalate_stale_dispatched()

    assert count == 1
    mock_status.assert_called_once_with("T-timeout-1", "sprint", worker="")


def test_timeout_marks_worker_on_cooldown(tmp_path):
    """Builder that timed out must be put on cooldown."""
    with (
        _patch_store([_stale_ticket("T-timeout-2", "dicksimnel")]),
        patch("devices.granny.availability._AVAILABLE_DIR", tmp_path),
        patch("devices.granny.daemon._setstatus_direct", return_value=True),
    ):
        from devices.granny.daemon import _escalate_stale_dispatched
        _escalate_stale_dispatched()

    cooldown_file = tmp_path / "DickSimnel.0.cooldown_until"
    false_flag = tmp_path / "DickSimnel.0.available.false"
    assert false_flag.exists(), "DickSimnel.0 must be marked unavailable after timeout"
    assert cooldown_file.exists(), "cooldown_until file must exist after timeout"


def test_timeout_unknown_worker_no_crash(tmp_path):
    """An unrecognised worker name must not crash _escalate_stale_dispatched."""
    with (
        _patch_store([_stale_ticket("T-timeout-3", "unknownrobot")]),
        patch("devices.granny.availability._AVAILABLE_DIR", tmp_path),
        patch("devices.granny.daemon._setstatus_direct", return_value=True),
    ):
        from devices.granny.daemon import _escalate_stale_dispatched
        count = _escalate_stale_dispatched()

    assert count == 1
    # No cooldown files — unknown worker is skipped silently
    assert not list(tmp_path.glob("*.cooldown_until"))
