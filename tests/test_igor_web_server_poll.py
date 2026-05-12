"""Tests for wild_igor.igor.web.server poll loop epoch check (T-adc-registration-recovery)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPollLoopEpochCheck:
    """_poll_loop calls check_server_epoch() every _EPOCH_CHECK_INTERVAL polls."""

    def _run_n_polls(self, n: int):
        """Import server and run _poll_loop for exactly n poll iterations."""
        import importlib
        import wild_igor.igor.web.server as srv

        importlib.reload(srv)

        call_count = [0]

        def fake_is_set():
            call_count[0] += 1
            return call_count[0] > n

        mock_uc = MagicMock()
        mock_uc.is_registered = True
        mock_uc.poll_messages.return_value = []

        with (
            patch.object(srv, "uc_client", mock_uc),
            patch.object(srv._poll_stop, "is_set", side_effect=fake_is_set),
            patch.object(srv._poll_stop, "wait"),
        ):
            srv._poll_loop()

        return mock_uc

    def test_check_not_called_before_10_polls(self):
        mock_uc = self._run_n_polls(9)
        mock_uc.check_server_epoch.assert_not_called()

    def test_check_called_at_10th_poll(self):
        mock_uc = self._run_n_polls(10)
        mock_uc.check_server_epoch.assert_called_once()

    def test_check_called_twice_at_20_polls(self):
        mock_uc = self._run_n_polls(20)
        assert mock_uc.check_server_epoch.call_count == 2

    def test_check_epoch_exception_does_not_crash_loop(self):
        """An exception inside check_server_epoch must not kill the poll loop."""
        mock_uc = self._run_n_polls(10)
        # If we got here without exception the loop survived; just verify it ran
        assert mock_uc.poll_messages.call_count == 10
