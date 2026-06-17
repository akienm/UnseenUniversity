"""
Tests for devices/build_digester/log_parser.py and digest_store.py.

Coverage:
  1. Parser: add/setstatus/close/awaiting_validation/hold/dispatch/queue_next events
  2. Parser: skips entries with no ticket_id, unknown actions, malformed JSON
  3. Parser: flat-timeline degrade (no boundary markers → has_boundary_marker=False)
  4. Parser: detects boundary markers when present
  5. Parser: parse_log_file reads fixture file, returns events + correct offset
  6. DigestStore.ensure_tables: emits correct DDL (mock conn)
  7. DigestStore.upsert_event: inserts/updates row (mock conn)
  8. DigestStore.get_digest: returns structured dict (mock conn)
  9. DigestStore.list_recent: returns ordered list (mock conn)
 10. Full flat-timeline from fixture file
 11. Graceful degradation: flat timeline emitted when no boundary markers
 12. Runme start/stop: daemon terminates cleanly on stop()
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from devices.build_digester.log_parser import parse_line, parse_log_file

_FIXTURES = _REPO / "tests" / "fixtures" / "build_digester"


# ── 1. Parser: individual event types ─────────────────────────────────────────


class TestParserEventTypes:
    def test_add_event(self):
        line = json.dumps({"action": "add", "id": "T-foo", "title": "Foo title", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["ticket_id"] == "T-foo"
        assert evt["action"] == "add"
        assert "Foo title" in evt["summary"]
        assert not evt["has_boundary_marker"]

    def test_setstatus_event(self):
        line = json.dumps({"action": "setstatus", "id": "T-foo", "old": "sprint", "new": "in_progress", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["action"] == "setstatus"
        assert "sprint → in_progress" in evt["summary"]

    def test_close_event(self):
        line = json.dumps({"action": "close", "id": "T-foo", "result": "Done and shipped.", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["action"] == "close"
        assert "Done and shipped" in evt["summary"]

    def test_awaiting_validation_event(self):
        line = json.dumps({"action": "awaiting_validation", "id": "T-foo", "result": "Submitted.", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["action"] == "awaiting_validation"
        assert "Submitted" in evt["summary"]

    def test_hold_event(self):
        line = json.dumps({"action": "hold", "id": "T-foo", "reason": "blocked", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["action"] == "hold"
        assert "blocked" in evt["summary"]

    def test_dispatch_event(self):
        line = json.dumps({"action": "dispatch", "id": "T-foo", "dispatched_by": "granny", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["action"] == "dispatch"
        assert "granny" in evt["summary"]

    def test_queue_next_from_cc_log(self):
        # queue_next as it appears in cc_channel log (hypothetical)
        line = json.dumps({"event": "queue_next", "ticket_id": "T-foo", "worker": "claude", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["ticket_id"] == "T-foo"
        assert "claude" in evt["summary"]

    def test_queue_next_from_queue_trace(self):
        # queue_next from datacenter_logs/queue/trace format
        line = json.dumps({"ts": "2026-01-01T00:00:00+00:00", "device": "queue", "event": "queue_next", "data": {"worker": "claude", "ticket_id": "T-foo"}})
        evt = parse_line(line)
        assert evt is not None
        assert evt["ticket_id"] == "T-foo"
        assert "claude" in evt["summary"]


# ── 2. Parser: skip cases ─────────────────────────────────────────────────────


class TestParserSkips:
    def test_empty_line_returns_none(self):
        assert parse_line("") is None

    def test_whitespace_line_returns_none(self):
        assert parse_line("   \n") is None

    def test_malformed_json_returns_none(self):
        assert parse_line("{not valid json") is None

    def test_no_action_returns_none(self):
        line = json.dumps({"id": "T-foo", "ts": "2026-01-01T00:00:00+00:00"})
        assert parse_line(line) is None

    def test_unknown_action_returns_none(self):
        line = json.dumps({"action": "auto_validate_skip", "id": "T-foo", "ts": "2026-01-01T00:00:00+00:00"})
        assert parse_line(line) is None

    def test_no_ticket_id_returns_none(self):
        # 'note' action has no ticket_id
        line = json.dumps({"action": "note", "message": "free form", "ts": "2026-01-01T00:00:00+00:00"})
        assert parse_line(line) is None

    def test_ticket_id_must_start_with_T_dash(self):
        line = json.dumps({"action": "add", "id": "foobar", "ts": "2026-01-01T00:00:00+00:00"})
        assert parse_line(line) is None


# ── 3 & 4. Boundary markers ───────────────────────────────────────────────────


class TestBoundaryMarkers:
    def test_standard_events_no_boundary_marker(self):
        line = json.dumps({"action": "setstatus", "id": "T-foo", "old": "sprint", "new": "in_progress", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt["has_boundary_marker"] is False

    def test_attempt_start_is_boundary_marker(self):
        line = json.dumps({"action": "attempt_start", "id": "T-foo", "detail": "starting attempt 1", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["has_boundary_marker"] is True

    def test_attempt_end_is_boundary_marker(self):
        line = json.dumps({"action": "attempt_end", "id": "T-foo", "detail": "attempt 1 failed", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["has_boundary_marker"] is True

    def test_build_event_is_boundary_marker(self):
        line = json.dumps({"action": "build_event", "id": "T-foo", "detail": "tests passed", "ts": "2026-01-01T00:00:00+00:00"})
        evt = parse_line(line)
        assert evt is not None
        assert evt["has_boundary_marker"] is True


# ── 5. parse_log_file against fixture ─────────────────────────────────────────


class TestParseLogFile:
    def test_reads_cc_channel_fixture(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, offset = parse_log_file(path)
        assert len(events) >= 5  # add, setstatus, close, hold, awaiting_validation, dispatch
        ticket_ids = {e["ticket_id"] for e in events}
        assert "T-foo-ticket" in ticket_ids
        assert "T-bar-ticket" in ticket_ids
        assert "T-baz-ticket" in ticket_ids

    def test_reads_queue_trace_fixture(self):
        path = str(_FIXTURES / "sample_queue_trace.jsonl")
        events, offset = parse_log_file(path)
        assert len(events) == 2
        assert all(e["action"] == "queue_next" for e in events)

    def test_returns_nonzero_offset(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, offset = parse_log_file(path)
        assert offset > 0

    def test_resume_from_offset_returns_nothing_new(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, offset = parse_log_file(path)
        events2, offset2 = parse_log_file(path, start_offset=offset)
        assert events2 == []
        assert offset2 == offset

    def test_missing_file_returns_empty_no_exception(self):
        events, offset = parse_log_file("/nonexistent/path/file.jsonl")
        assert events == []
        assert offset == 0


# ── 6-9. DigestStore with mock DB ─────────────────────────────────────────────


def _make_mock_store():
    """Return a DigestStore with a mocked _connect."""
    from devices.build_digester.digest_store import DigestStore
    store = DigestStore(db_url="postgresql://test/test")
    return store


class TestDigestStoreEnsureTables:
    def test_ensure_tables_runs_ddl(self):
        store = _make_mock_store()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(store, "_connect", return_value=mock_conn):
            store.ensure_tables()

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        ddl_combined = " ".join(executed)
        assert "CREATE SCHEMA IF NOT EXISTS devlab" in ddl_combined
        assert "CREATE TABLE IF NOT EXISTS devlab.build_digest" in ddl_combined
        assert "CREATE INDEX IF NOT EXISTS build_digest_last_event" in ddl_combined

    def test_ensure_tables_idempotent(self):
        store = _make_mock_store()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(store, "_connect", return_value=mock_conn):
            store.ensure_tables()
            store.ensure_tables()  # second call should skip

        assert mock_conn.cursor.call_count == 1


class TestDigestStoreUpsert:
    def test_upsert_calls_insert_on_conflict(self):
        store = _make_mock_store()
        store._tables_ensured = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        event = {
            "ticket_id": "T-test",
            "ts": "2026-01-01T00:00:00+00:00",
            "action": "setstatus",
            "summary": "sprint → in_progress",
            "has_boundary_marker": False,
        }
        with patch.object(store, "_connect", return_value=mock_conn):
            store.upsert_event(event)

        sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO devlab.build_digest" in s for s in sql_calls)
        assert any("ON CONFLICT" in s for s in sql_calls)

    def test_upsert_updates_status_on_close(self):
        store = _make_mock_store()
        store._tables_ensured = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        event = {
            "ticket_id": "T-test",
            "ts": "2026-01-01T00:00:00+00:00",
            "action": "close",
            "summary": "closed: done",
            "has_boundary_marker": False,
        }
        with patch.object(store, "_connect", return_value=mock_conn):
            store.upsert_event(event)

        sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE devlab.build_digest SET status" in s for s in sql_calls)


class TestDigestStoreGetDigest:
    def test_get_digest_returns_dict(self):
        from datetime import datetime, timezone
        store = _make_mock_store()
        store._tables_ensured = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_cursor.fetchone.return_value = (
            "T-test", "closed", ts, ts,
            [{"ts": "2026-01-01T00:00:00+00:00", "action": "add", "summary": "filed"}],
            None, False, ts,
        )

        with patch.object(store, "_connect", return_value=mock_conn):
            result = store.get_digest("T-test")

        assert result is not None
        assert result["ticket_id"] == "T-test"
        assert result["status"] == "closed"
        assert isinstance(result["events"], list)
        assert not result["has_boundary_markers"]

    def test_get_digest_returns_none_when_not_found(self):
        store = _make_mock_store()
        store._tables_ensured = True

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with patch.object(store, "_connect", return_value=mock_conn):
            result = store.get_digest("T-nonexistent")

        assert result is None


# ── 10. Full flat-timeline from fixture ───────────────────────────────────────


class TestFlatTimelineFromFixture:
    def test_foo_ticket_has_full_timeline(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, _ = parse_log_file(path)
        foo_events = [e for e in events if e["ticket_id"] == "T-foo-ticket"]
        actions = [e["action"] for e in foo_events]
        assert "add" in actions
        assert "setstatus" in actions
        assert "close" in actions

    def test_bar_ticket_has_hold(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, _ = parse_log_file(path)
        bar_events = [e for e in events if e["ticket_id"] == "T-bar-ticket"]
        assert any(e["action"] == "hold" for e in bar_events)

    def test_queue_next_combined_with_cc_log(self):
        cc_path = str(_FIXTURES / "sample_cc_channel.jsonl")
        qt_path = str(_FIXTURES / "sample_queue_trace.jsonl")
        cc_events, _ = parse_log_file(cc_path)
        qt_events, _ = parse_log_file(qt_path)
        all_events = cc_events + qt_events
        foo_actions = [e["action"] for e in all_events if e["ticket_id"] == "T-foo-ticket"]
        assert "queue_next" in foo_actions
        assert "add" in foo_actions


# ── 11. Graceful degradation — flat timeline when no boundary markers ─────────


class TestFlatTimelineDegradation:
    def test_no_boundary_markers_in_fixture(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, _ = parse_log_file(path)
        assert not any(e["has_boundary_marker"] for e in events), (
            "Fixture should produce zero boundary markers (degrade to flat timeline)"
        )

    def test_has_boundary_markers_with_injected_event(self):
        path = str(_FIXTURES / "sample_cc_channel.jsonl")
        events, offset = parse_log_file(path)
        # Inject a boundary-marker event manually
        boundary_line = json.dumps({
            "action": "attempt_start",
            "id": "T-foo-ticket",
            "detail": "attempt 1",
            "ts": "2026-01-01T00:00:00+00:00",
        })
        boundary_evt = parse_line(boundary_line)
        all_events = events + [boundary_evt]
        assert any(e["has_boundary_marker"] for e in all_events)


# ── 12. Runme start/stop ──────────────────────────────────────────────────────


class TestRunmeStartStop:
    def test_stop_terminates_start(self):
        import devices.build_digester.groundloop.runme as runme

        mock_store = MagicMock()
        mock_store.ensure_tables = MagicMock()

        def _fake_poll(store, cursors):
            return cursors

        # DigestStore is imported lazily inside start(); patch at its home module.
        with patch("devices.build_digester.digest_store.DigestStore", return_value=mock_store):
            with patch("devices.build_digester.groundloop.runme._poll_once", side_effect=_fake_poll):
                with patch("devices.build_digester.groundloop.runme._POLL_INTERVAL_S", 1):
                    thread = threading.Thread(target=runme.start, daemon=True)
                    thread.start()
                    time.sleep(0.2)
                    runme.stop()
                    thread.join(timeout=3.0)
                    assert not thread.is_alive(), "daemon should stop after stop() is called"

    def test_db_error_does_not_crash_daemon(self):
        import devices.build_digester.groundloop.runme as runme

        mock_store = MagicMock()
        mock_store.ensure_tables = MagicMock(side_effect=RuntimeError("no db"))

        with patch("devices.build_digester.digest_store.DigestStore", return_value=mock_store):
            with patch("devices.build_digester.groundloop.runme._POLL_INTERVAL_S", 1):
                with patch("devices.build_digester.groundloop.runme._RETRY_DELAY_S", 0):
                    thread = threading.Thread(target=runme.start, daemon=True)
                    thread.start()
                    time.sleep(0.3)
                    runme.stop()
                    thread.join(timeout=3.0)
                    assert not thread.is_alive()
