"""
test_stale_chat_log_backfiller.py — T-cc-mirror-5min-today

Cadence + invocation tests for StaleChatLogBackfiller.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.claude.stale_chat_log_backfiller import (  # noqa: E402
    StaleChatLogBackfiller,
)


class TestStaleChatLogBackfiller:
    def test_refresh_interval_is_5min(self):
        assert StaleChatLogBackfiller.REFRESH_INTERVAL_SEC == 300

    def test_first_call_runs(self):
        src = StaleChatLogBackfiller()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="wrote 1 file", stderr=""
            )
            src.run()
        assert mock_run.called
        # No --all flag in args
        args = mock_run.call_args.args[0]
        assert "--all" not in args

    def test_skip_within_interval(self):
        src = StaleChatLogBackfiller()
        src._last_run = datetime.now(timezone.utc) - timedelta(seconds=60)
        with patch("subprocess.run") as mock_run:
            src.run()
        assert not mock_run.called

    def test_runs_after_interval(self):
        src = StaleChatLogBackfiller()
        # 6 min ago — past the 5-min threshold
        src._last_run = datetime.now(timezone.utc) - timedelta(seconds=360)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="wrote 1 file", stderr=""
            )
            src.run()
        assert mock_run.called

    def test_failure_handling(self):
        src = StaleChatLogBackfiller()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            src.run()
        # No exception should be raised

    def test_timing_tier_slow(self):
        assert StaleChatLogBackfiller.TIMING_TIER == "slow"
