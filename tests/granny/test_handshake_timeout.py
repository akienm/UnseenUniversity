"""Tests for Gap A: dispatch timeout resets to sprint + builder cooldown."""

import sys
from unittest.mock import MagicMock, patch


def _stub_pg_connect(stale_rows):
    """Patch psycopg2.connect to return rows from stale_rows on fetchall()."""
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = stale_rows
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_pg = MagicMock()
    mock_pg.connect.return_value = mock_conn
    mock_pg.extras.RealDictCursor = None
    sys.modules["psycopg2"] = mock_pg
    sys.modules["psycopg2.extras"] = mock_pg.extras
    return mock_pg


def test_timeout_resets_to_sprint_not_escalated(tmp_path):
    """A timed-out dispatched ticket must go to 'sprint', not 'escalated'."""
    stale_rows = [{"tid": "T-timeout-1", "worker": "dicksimnel"}]
    _stub_pg_connect(stale_rows)

    with (
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
    stale_rows = [{"tid": "T-timeout-2", "worker": "dicksimnel"}]
    _stub_pg_connect(stale_rows)

    with (
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
    stale_rows = [{"tid": "T-timeout-3", "worker": "unknownrobot"}]
    _stub_pg_connect(stale_rows)

    with (
        patch("devices.granny.availability._AVAILABLE_DIR", tmp_path),
        patch("devices.granny.daemon._setstatus_direct", return_value=True),
    ):
        from devices.granny.daemon import _escalate_stale_dispatched
        count = _escalate_stale_dispatched()

    assert count == 1
    # No cooldown files — unknown worker is skipped silently
    assert not list(tmp_path.glob("*.cooldown_until"))
