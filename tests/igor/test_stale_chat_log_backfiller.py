"""
test_stale_chat_log_backfiller.py — T-cc-mirror-5min-today

Cadence + invocation tests for StaleChatLogBackfiller.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.push_sources import (  # noqa: E402
    StaleChatLogBackfiller,
)


class TestStaleChatLogBackfiller:
    def test_refresh_interval_is_5min(self):
        assert StaleChatLogBackfiller.REFRESH_INTERVAL_SEC == 300

    def test_first_call_runs(self):
        src = StaleChatLogBackfiller()
        cortex = MagicMock()
        cortex.twm_push.return_value = 1
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="wrote 1 file", stderr=""
            )
            src.push(cortex)
        assert mock_run.called
        # No --all flag in args
        args = mock_run.call_args.args[0]
        assert "--all" not in args

    def test_skip_within_interval(self):
        src = StaleChatLogBackfiller()
        src._last_run = datetime.now(timezone.utc) - timedelta(seconds=60)
        cortex = MagicMock()
        with patch("subprocess.run") as mock_run:
            ids = src.push(cortex)
        assert ids == []
        assert not mock_run.called

    def test_runs_after_interval(self):
        src = StaleChatLogBackfiller()
        # 6 min ago — past the 5-min threshold
        src._last_run = datetime.now(timezone.utc) - timedelta(seconds=360)
        cortex = MagicMock()
        cortex.twm_push.return_value = 1
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="wrote 1 file", stderr=""
            )
            src.push(cortex)
        assert mock_run.called

    def test_failure_logs_error(self):
        src = StaleChatLogBackfiller()
        cortex = MagicMock()
        with patch("subprocess.run") as mock_run, patch(
            "devices.igor.cognition.push_sources.log_error"
        ) as mock_log:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            src.push(cortex)
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs.get("kind") == "CHAT_LOG_EXPORT_FAIL"

    def test_timing_tier_slow(self):
        assert StaleChatLogBackfiller.TIMING_TIER == "slow"
