"""Tests for nighttime_auditor — T-adc-nighttime-auditor."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
)


def _db_reachable() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"], connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Igor DB not reachable")


def _make_row(tool_name, exit_code=0, args_json=None, result_summary="ok"):
    from datetime import datetime, timezone

    return {
        "tool_name": tool_name,
        "device_id": "librarian",
        "args_json": args_json or {},
        "result_summary": result_summary,
        "exit_code": exit_code,
        "duration_ms": 10,
        "ts": datetime.now(timezone.utc),
    }


class TestBuildSummary:
    def test_empty_rows_returns_empty(self):
        from unseen_university.devices.librarian.nighttime_auditor import _build_summary

        assert _build_summary([]) == ""

    def test_summary_contains_tool_counts(self):
        from unseen_university.devices.librarian.nighttime_auditor import _build_summary

        rows = [
            _make_row("shell_exec"),
            _make_row("shell_exec"),
            _make_row("file_read"),
        ]
        summary = _build_summary(rows)
        assert "shell_exec: 2" in summary
        assert "file_read: 1" in summary

    def test_failed_executions_highlighted(self):
        from unseen_university.devices.librarian.nighttime_auditor import _build_summary

        rows = [_make_row("shell_exec", exit_code=1)]
        summary = _build_summary(rows)
        assert "Failed executions" in summary
        assert "exit=1" in summary

    def test_high_volume_flagged(self):
        from unseen_university.devices.librarian.nighttime_auditor import (
            _HIGH_VOLUME_THRESHOLD,
            _build_summary,
        )

        rows = [_make_row("shell_exec")] * (_HIGH_VOLUME_THRESHOLD + 1)
        summary = _build_summary(rows)
        assert "HIGH VOLUME" in summary or "Potential loop" in summary

    def test_tickets_filed_listed(self):
        from unseen_university.devices.librarian.nighttime_auditor import _build_summary

        rows = [
            _make_row(
                "file_ticket",
                args_json={"ticket_id": "T-test-xyz", "title": "Test ticket"},
            )
        ]
        summary = _build_summary(rows)
        assert "Tickets filed" in summary
        assert "T-test-xyz" in summary

    def test_large_write_flagged(self):
        from unseen_university.devices.librarian.nighttime_auditor import (
            _LARGE_WRITE_BYTES,
            _build_summary,
        )

        rows = [
            _make_row(
                "file_write",
                args_json={"path": "/tmp/big.txt"},
                result_summary=f"written={_LARGE_WRITE_BYTES + 1}",
            )
        ]
        summary = _build_summary(rows)
        assert "Large file write" in summary


class TestRunAudit:
    def test_no_post_when_empty(self):
        from unseen_university.devices.librarian import nighttime_auditor as na

        with patch.object(na, "_fetch_window", return_value=[]):
            with patch.object(na, "_post_to_channel") as mock_post:
                result = na.run_audit()
                assert result is None
                mock_post.assert_not_called()

    def test_posts_once_when_rows_present(self):
        from unseen_university.devices.librarian import nighttime_auditor as na

        rows = [_make_row("shell_exec"), _make_row("file_read")]
        with patch.object(na, "_fetch_window", return_value=rows):
            with patch.object(na, "_post_to_channel") as mock_post:
                result = na.run_audit()
                assert result is not None
                mock_post.assert_called_once()
                msg = mock_post.call_args[0][0]
                assert "nighttime-auditor" in msg


class TestStartAuditor:
    def test_thread_starts_on_initialize(self):
        """start_nighttime_auditor() starts exactly one daemon thread."""
        import unseen_university.devices.librarian.nighttime_auditor as na

        # Reset the guard so we can test start
        na._started = False
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_thread_cls.return_value = mock_t
            na.start_nighttime_auditor()
            mock_thread_cls.assert_called_once()
            mock_t.start.assert_called_once()
        na._started = False  # clean up for other tests
