"""Tests for Gap C: dispatch_done envelope handling in _process_handshake_replies."""

from unittest.mock import MagicMock, patch


def _make_envelope(kind, ticket_id, from_device="dicksimnel.0"):
    env = MagicMock()
    env.payload = {"kind": kind, "ticket_id": ticket_id, "from_device": from_device}
    return env


def test_dispatch_done_logs_no_status_change():
    """dispatch_done must log at INFO but NOT call _setstatus_direct."""
    imap = MagicMock()
    imap.fetch_unseen.return_value = [_make_envelope("dispatch_done", "T-done-1")]

    with patch("unseen_university.devices.granny.daemon._setstatus_direct") as mock_status:
        from unseen_university.devices.granny.daemon import _process_handshake_replies
        count = _process_handshake_replies(imap, "granny.0")

    assert count == 1
    mock_status.assert_not_called()


def test_dispatch_done_mixed_with_other_replies():
    """dispatch_done must not interfere with ack/started processing in the same batch."""
    imap = MagicMock()
    imap.fetch_unseen.return_value = [
        _make_envelope("dispatch_ack", "T-ack-1"),
        _make_envelope("dispatch_done", "T-done-2"),
        _make_envelope("dispatch_started", "T-started-1"),
    ]

    calls = []
    with patch("unseen_university.devices.granny.daemon._setstatus_direct", side_effect=lambda tid, status, **kw: calls.append((tid, status)) or True):
        from unseen_university.devices.granny.daemon import _process_handshake_replies
        count = _process_handshake_replies(imap, "granny.0")

    assert count == 3
    assert ("T-ack-1", "acked") in calls
    assert ("T-started-1", "in_progress") in calls
    # dispatch_done must NOT produce a status call
    statused_ids = [c[0] for c in calls]
    assert "T-done-2" not in statused_ids
