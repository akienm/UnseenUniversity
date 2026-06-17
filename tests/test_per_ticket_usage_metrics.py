"""
Tests for lab/claudecode/usage_store.py — per-ticket token/usage actuals.

Coverage:
  1. _read_sprint_log_entries: parses correct lines, skips wrong ticket_id, tolerates missing file
  2. _aggregate_entries: sums tokens across sessions
  3. UsageStore.ensure_tables: emits correct DDL (mock conn)
  4. UsageStore.record: inserts row when log entries exist (mock conn)
  5. UsageStore.record: returns False when no log entries exist
  6. UsageStore.get_by_ticket: returns list of rows (mock conn)
  7. UsageStore.get_aggregate: returns summary dict (mock conn)
  8. _record_ticket_usage in cc_queue: non-fatal on UsageStore failure
  9. cc_queue.cmd_close calls _record_ticket_usage
 10. wall_clock_s computed when started_at and closed_at provided
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab" / "claudecode"))

from lab.claudecode.usage_store import (
    _read_sprint_log_entries,
    _aggregate_entries,
    UsageStore,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sprint_log(entries: list[list]) -> Path:
    """Write sprint_tokens.log lines to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    )
    for e in entries:
        tmp.write("|".join(str(x) for x in e) + "\n")
    tmp.close()
    return Path(tmp.name)


def _make_mock_store(db_url: str = "postgresql://test/test") -> UsageStore:
    store = UsageStore(db_url=db_url)
    return store


def _make_mock_conn():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


# ── 1. _read_sprint_log_entries ───────────────────────────────────────────────


class TestReadSprintLogEntries:
    _LINE = ["2026-01-01T00:00:00+00:00", "T-foo", "100", "50", "200", "80", "claude-sonnet-4-6"]

    def test_reads_matching_ticket(self):
        path = _make_sprint_log([self._LINE])
        try:
            entries = _read_sprint_log_entries("T-foo", log_path=path)
            assert len(entries) == 1
            assert entries[0]["input_tokens"] == 100
            assert entries[0]["cache_write_tokens"] == 50
            assert entries[0]["cache_read_tokens"] == 200
            assert entries[0]["output_tokens"] == 80
            assert entries[0]["model"] == "claude-sonnet-4-6"
        finally:
            path.unlink(missing_ok=True)

    def test_skips_other_ticket(self):
        other = ["2026-01-01T00:00:00+00:00", "T-bar", "100", "50", "200", "80", "claude-sonnet-4-6"]
        path = _make_sprint_log([self._LINE, other])
        try:
            entries = _read_sprint_log_entries("T-foo", log_path=path)
            assert len(entries) == 1
            assert entries[0]["ticket_id"] == "T-foo"
        finally:
            path.unlink(missing_ok=True)

    def test_returns_empty_for_missing_file(self):
        entries = _read_sprint_log_entries("T-foo", log_path=Path("/nonexistent/path.log"))
        assert entries == []

    def test_multiple_sessions_for_same_ticket(self):
        path = _make_sprint_log([self._LINE, self._LINE])
        try:
            entries = _read_sprint_log_entries("T-foo", log_path=path)
            assert len(entries) == 2
        finally:
            path.unlink(missing_ok=True)

    def test_tolerates_short_lines(self):
        bad_line = ["2026-01-01T00:00:00+00:00", "T-foo", "100"]
        path = _make_sprint_log([bad_line, self._LINE])
        try:
            entries = _read_sprint_log_entries("T-foo", log_path=path)
            assert len(entries) == 1  # short line skipped
        finally:
            path.unlink(missing_ok=True)


# ── 2. _aggregate_entries ─────────────────────────────────────────────────────


class TestAggregateEntries:
    def test_sums_single_entry(self):
        entries = [{
            "ts": "2026-01-01T00:00:00+00:00",
            "ticket_id": "T-foo",
            "input_tokens": 100,
            "cache_write_tokens": 50,
            "cache_read_tokens": 200,
            "output_tokens": 80,
            "model": "claude-sonnet-4-6",
        }]
        agg = _aggregate_entries(entries)
        assert agg["input_tokens"] == 100
        assert agg["total_tokens"] == 430  # 100+50+200+80
        assert agg["model"] == "claude-sonnet-4-6"

    def test_sums_multiple_entries(self):
        entry = {
            "ts": "2026-01-01T00:00:00+00:00",
            "ticket_id": "T-foo",
            "input_tokens": 100,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "output_tokens": 50,
            "model": "claude-sonnet-4-6",
        }
        agg = _aggregate_entries([entry, entry])
        assert agg["input_tokens"] == 200
        assert agg["total_tokens"] == 300

    def test_uses_last_model(self):
        entries = [
            {"ts": "", "ticket_id": "T-foo", "input_tokens": 0, "cache_write_tokens": 0,
             "cache_read_tokens": 0, "output_tokens": 0, "model": "claude-haiku"},
            {"ts": "", "ticket_id": "T-foo", "input_tokens": 0, "cache_write_tokens": 0,
             "cache_read_tokens": 0, "output_tokens": 0, "model": "claude-sonnet-4-6"},
        ]
        agg = _aggregate_entries(entries)
        assert agg["model"] == "claude-sonnet-4-6"


# ── 3. ensure_tables DDL ──────────────────────────────────────────────────────


class TestEnsureTables:
    def test_ensure_tables_runs_ddl(self):
        store = _make_mock_store()
        mock_conn, mock_cursor = _make_mock_conn()

        with patch.object(store, "_connect", return_value=mock_conn):
            store.ensure_tables()

        executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
        combined = " ".join(executed)
        assert "CREATE SCHEMA IF NOT EXISTS devlab" in combined
        assert "CREATE TABLE IF NOT EXISTS devlab.ticket_usage" in combined
        assert "ticket_id" in combined
        assert "total_tokens" in combined

    def test_ensure_tables_idempotent(self):
        store = _make_mock_store()
        mock_conn, mock_cursor = _make_mock_conn()

        with patch.object(store, "_connect", return_value=mock_conn):
            store.ensure_tables()
            store.ensure_tables()

        assert mock_conn.cursor.call_count == 1


# ── 4. record: inserts row when log entries exist ─────────────────────────────


class TestUsageStoreRecord:
    def _make_log(self) -> Path:
        return _make_sprint_log([
            ["2026-01-01T00:00:00+00:00", "T-test", "500", "200", "1000", "300", "claude-sonnet-4-6"]
        ])

    def test_record_inserts_row(self):
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()
        path = self._make_log()

        try:
            with patch.object(store, "_connect", return_value=mock_conn):
                result = store.record("T-test", worker="claude", cost_usd=0.01, log_path=path)
        finally:
            path.unlink(missing_ok=True)

        assert result is True
        sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO devlab.ticket_usage" in s for s in sql_calls)

    def test_record_returns_false_when_no_log_data(self):
        store = _make_mock_store()
        store._tables_ensured = True

        result = store.record("T-empty", log_path=Path("/nonexistent.log"))
        assert result is False

    def test_record_sets_provider_anthropic_by_default(self):
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()
        path = self._make_log()

        try:
            with patch.object(store, "_connect", return_value=mock_conn):
                store.record("T-test", worker="claude", log_path=path)
        finally:
            path.unlink(missing_ok=True)

        all_params = [c.args[1] for c in mock_cursor.execute.call_args_list if c.args[1:]]
        flat = [str(p) for params in all_params for p in (params if isinstance(params, (list, tuple)) else [params])]
        assert "anthropic" in flat


# ── 5. wall_clock_s calculation ───────────────────────────────────────────────


class TestWallClockCalc:
    def test_wall_clock_computed_from_timestamps(self):
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()
        path = _make_sprint_log([
            ["2026-01-01T00:00:00+00:00", "T-clock", "100", "0", "0", "50", "claude-sonnet-4-6"]
        ])

        try:
            with patch.object(store, "_connect", return_value=mock_conn):
                store.record(
                    "T-clock",
                    started_at="2026-01-01T10:00:00+00:00",
                    closed_at="2026-01-01T10:30:00+00:00",
                    log_path=path,
                )
        finally:
            path.unlink(missing_ok=True)

        all_params = [c.args[1] for c in mock_cursor.execute.call_args_list if c.args[1:]]
        params_flat = [p for params in all_params if params for p in params]
        # wall_clock_s = 30 * 60 = 1800
        assert 1800 in params_flat


# ── 6. get_by_ticket ──────────────────────────────────────────────────────────


class TestGetByTicket:
    def test_returns_list_of_rows(self):
        from datetime import datetime, timezone
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()

        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_cursor.fetchall.return_value = [
            (1, "T-foo", "claude", "anthropic", "claude-sonnet-4-6",
             ts, ts, 500, 200, 1000, 300, 2000, 0.01, 1800, ts)
        ]

        with patch.object(store, "_connect", return_value=mock_conn):
            rows = store.get_by_ticket("T-foo")

        assert len(rows) == 1
        assert rows[0]["ticket_id"] == "T-foo"
        assert rows[0]["total_tokens"] == 2000
        assert rows[0]["cost_usd"] == pytest.approx(0.01)
        assert rows[0]["wall_clock_s"] == 1800

    def test_returns_empty_list_when_not_found(self):
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchall.return_value = []

        with patch.object(store, "_connect", return_value=mock_conn):
            rows = store.get_by_ticket("T-nonexistent")

        assert rows == []


# ── 7. get_aggregate ──────────────────────────────────────────────────────────


class TestGetAggregate:
    def test_returns_summary_dict(self):
        store = _make_mock_store()
        store._tables_ensured = True
        mock_conn, mock_cursor = _make_mock_conn()
        mock_cursor.fetchone.return_value = (5, 50000, 2.50, 1800.0)

        with patch.object(store, "_connect", return_value=mock_conn):
            agg = store.get_aggregate()

        assert agg["tickets_last_30d"] == 5
        assert agg["total_tokens_last_30d"] == 50000
        assert agg["total_cost_usd_last_30d"] == pytest.approx(2.50)
        assert agg["avg_wall_clock_s"] == pytest.approx(1800.0)


# ── 8. cc_queue._record_ticket_usage non-fatal ───────────────────────────────


class TestCCQueueRecordTicketUsage:
    def test_record_ticket_usage_nonfatal_on_failure(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cc_queue", _REPO / "lab" / "claudecode" / "cc_queue.py"
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't exec_module — just test the helper in isolation
        ticket = {"worker": "claude", "dispatched_at": None, "completed_at": None}

        with patch("lab.claudecode.usage_store.UsageStore") as MockStore:
            MockStore.return_value.record.side_effect = RuntimeError("DB down")
            # Should not raise
            from lab.claudecode import cc_queue as cq
            cq._record_ticket_usage("T-test", ticket, cost_usd=0.01)


# ── 9. cc_queue.cmd_close calls _record_ticket_usage ─────────────────────────


class TestCCQueueCmdCloseCallsRecord:
    def test_cmd_close_calls_record_ticket_usage(self, tmp_path, monkeypatch):
        from lab.claudecode import cc_queue as cq

        ticket = {
            "id": "T-closetest",
            "status": "in_progress",
            "title": "Test close ticket",
            "result": None,
            "completed_at": None,
            "worker": "claude",
            "cost_usd": None,
        }

        monkeypatch.setattr(cq, "_load", lambda: [ticket])
        monkeypatch.setattr(cq, "_save", lambda t: None)
        monkeypatch.setattr(cq, "_log", lambda e: None)
        monkeypatch.setattr(cq, "_compute_cost_usd", lambda tid: 0.01)
        monkeypatch.setattr(cq, "_decision_rollup", lambda tasks, did: None)
        monkeypatch.setattr(cq, "_ungate_dependents", lambda tasks, tid: None)
        monkeypatch.setattr(cq, "_prepend_closed_ticket", lambda tid, title: None)
        monkeypatch.setattr(cq, "_close_igor_goal", lambda tid: None)
        monkeypatch.setattr(cq, "_classifier_clear_in_flight", lambda tid: None)
        monkeypatch.setattr(cq, "_annotator_delta_update", lambda tid: None)
        monkeypatch.setattr(cq, "_append_to_todays_slate", lambda t: None)

        called_with = {}

        def _mock_record(ticket_id, ticket_row, cost_usd=None):
            called_with["ticket_id"] = ticket_id
            called_with["cost_usd"] = cost_usd

        monkeypatch.setattr(cq, "_record_ticket_usage", _mock_record)
        monkeypatch.setattr(cq, "_with_status_prefix", lambda s, t: t)

        cq.cmd_close(["T-closetest", "shipped it"])

        assert called_with.get("ticket_id") == "T-closetest"
        assert called_with.get("cost_usd") == pytest.approx(0.01)
