"""
test_memory_snapshot.py — Tests for nightly memory snapshot tool (T-nightly-memory-count).
"""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

from unseen_university.devices.igor.tools.memory_snapshot import run_memory_snapshot


class TestMemorySnapshot:
    def test_skips_before_2200(self):
        with patch("unseen_university.devices.igor.tools.memory_snapshot.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=19)
            mock_dt.now().__class__ = datetime
            result = run_memory_snapshot()
        assert "skipped" in result
        assert "hour=19" in result

    def test_skips_if_already_ran_today(self, tmp_path):
        stamp = tmp_path / "memory_count.last_run"
        today = datetime.now().date().isoformat()
        stamp.write_text(today)

        with (
            patch("unseen_university.devices.igor.tools.memory_snapshot.datetime") as mock_dt,
            patch("unseen_university.devices.igor.tools.memory_snapshot._STAMP_FILE", stamp),
        ):
            mock_dt.now.return_value = MagicMock(hour=23)
            mock_dt.now(timezone.utc if False else None)
            # Use real datetime for date
            mock_dt.now.side_effect = lambda tz=None: (
                MagicMock(
                    hour=23,
                    date=lambda: datetime.now().date(),
                    isoformat=lambda: datetime.now().isoformat(),
                )
            )
            result = run_memory_snapshot()
        assert "already ran today" in result

    def test_runs_and_returns_summary(self, tmp_path):
        stamp = tmp_path / "memory_count.last_run"
        log = tmp_path / "memory_count.log"

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (53000,)
        mock_cursor.fetchall.return_value = [
            ("EPISODIC", 32000),
            ("FACTUAL", 16000),
            ("PROCEDURAL", 400),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with (
            patch("unseen_university.devices.igor.tools.memory_snapshot.datetime") as mock_dt,
            patch("unseen_university.devices.igor.tools.memory_snapshot._STAMP_FILE", stamp),
            patch("psycopg2.connect", return_value=mock_conn),
        ):
            mock_dt.now.side_effect = lambda tz=None: MagicMock(
                hour=23,
                date=lambda: datetime.now().date(),
                isoformat=lambda: "2026-04-03T23:00:00+00:00",
            )
            result = run_memory_snapshot()

        assert "53000" in result
        assert "EPISODIC" in result
        assert stamp.read_text() == datetime.now().date().isoformat()
