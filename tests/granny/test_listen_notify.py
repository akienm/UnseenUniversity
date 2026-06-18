"""
tests/granny/test_listen_notify.py — _setup_listen_notify / _wait_for_notify unit tests.

Tests:
- _setup_listen_notify returns None when DB connect fails (graceful degradation)
- _wait_for_notify(None, t) calls time.sleep(t) — pure polling fallback
- _wait_for_notify(conn, t) calls select.select with the connection as read fd
- _wait_for_notify fires conn.poll() when select returns ready
- _wait_for_notify logs NOTIFY wakeup when notifies queue is non-empty
- _wait_for_notify falls back to time.sleep on select/poll error
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


# ── _setup_listen_notify ───────────────────────────────────────────────────────


def test_setup_returns_none_on_connect_failure():
    with patch("psycopg2.connect", side_effect=Exception("connection refused")):
        from devices.granny.daemon import _setup_listen_notify
        result = _setup_listen_notify()
    assert result is None


def test_setup_returns_connection_on_success():
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("psycopg2.connect", return_value=mock_conn):
        from devices.granny.daemon import _setup_listen_notify
        result = _setup_listen_notify()

    assert result is mock_conn
    mock_conn.set_isolation_level.assert_called_once_with(0)


def test_setup_executes_listen(monkeypatch):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("psycopg2.connect", return_value=mock_conn):
        from devices.granny.daemon import _setup_listen_notify
        _setup_listen_notify()

    # Last execute call should be LISTEN
    calls = [c.args[0] for c in mock_cur.execute.call_args_list]
    assert any("LISTEN" in c for c in calls), f"No LISTEN in calls: {calls}"


# ── _wait_for_notify ──────────────────────────────────────────────────────────


def test_wait_sleeps_when_no_listen_conn():
    with patch("time.sleep") as mock_sleep:
        from devices.granny.daemon import _wait_for_notify
        _wait_for_notify(None, 30)
    mock_sleep.assert_called_once_with(30)


def test_wait_calls_select_with_connection():
    mock_conn = MagicMock()
    mock_conn.notifies = []

    with patch("devices.granny.daemon._select_module.select", return_value=([], [], [])) as mock_select:
        from devices.granny.daemon import _wait_for_notify
        _wait_for_notify(mock_conn, 30)

    mock_select.assert_called_once_with([mock_conn], [], [], 30)


def test_wait_polls_when_select_ready():
    mock_conn = MagicMock()
    mock_conn.notifies = []

    with patch("devices.granny.daemon._select_module.select", return_value=([mock_conn], [], [])):
        from devices.granny.daemon import _wait_for_notify
        _wait_for_notify(mock_conn, 30)

    mock_conn.poll.assert_called_once()


def test_wait_logs_notify_wakeup():
    mock_conn = MagicMock()
    fake_notify = MagicMock()
    fake_notify.channel = "ticket_queue_insert"
    fake_notify.payload = "T-some-ticket"
    mock_conn.notifies = [fake_notify]

    with patch("devices.granny.daemon._select_module.select", return_value=([mock_conn], [], [])), \
         patch("devices.granny.daemon.log") as mock_log:
        from devices.granny.daemon import _wait_for_notify
        _wait_for_notify(mock_conn, 30)

    assert any("NOTIFY" in str(c) for c in mock_log.info.call_args_list), \
        "Expected NOTIFY wakeup log"


def test_wait_falls_back_to_sleep_on_error():
    mock_conn = MagicMock()

    with patch("devices.granny.daemon._select_module.select", side_effect=OSError("fd error")), \
         patch("time.sleep") as mock_sleep:
        from devices.granny.daemon import _wait_for_notify
        _wait_for_notify(mock_conn, 30)

    mock_sleep.assert_called_once_with(30)
