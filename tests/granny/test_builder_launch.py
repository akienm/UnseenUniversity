"""Tests for Gap D: _launch_builder rate limiting and launch_cmd config."""

import time
from unittest.mock import MagicMock, call, patch


def test_launch_fires_when_launch_cmd_set(tmp_path):
    """_launch_builder must call Popen when launch_cmd is set and not rate-limited."""
    import unseen_university.devices.granny.daemon as daemon
    daemon._last_launch_attempt.clear()

    with patch("unseen_university.devices.granny.daemon.subprocess.Popen") as mock_popen:
        daemon._launch_builder("DickSimnel.0", {"launch_cmd": "echo test"})

    mock_popen.assert_called_once()
    cmd_arg = mock_popen.call_args[0][0]
    assert cmd_arg == "echo test"


def test_launch_skipped_when_no_launch_cmd(tmp_path):
    """_launch_builder must be a no-op when launch_cmd is null/absent."""
    import unseen_university.devices.granny.daemon as daemon
    daemon._last_launch_attempt.clear()

    with patch("unseen_university.devices.granny.daemon.subprocess.Popen") as mock_popen:
        daemon._launch_builder("CC.0", {"launch_cmd": None})
        daemon._launch_builder("CC.0", {})

    mock_popen.assert_not_called()


def test_launch_rate_limited_within_retry_window():
    """Second launch attempt within GRANNY_BUILDER_LAUNCH_RETRY_S must be suppressed."""
    import unseen_university.devices.granny.daemon as daemon
    daemon._last_launch_attempt.clear()

    with patch("unseen_university.devices.granny.daemon.subprocess.Popen") as mock_popen:
        daemon._launch_builder("DickSimnel.0", {"launch_cmd": "echo first"})
        daemon._launch_builder("DickSimnel.0", {"launch_cmd": "echo second"})

    # Only the first call should fire
    assert mock_popen.call_count == 1


def test_launch_allowed_after_retry_window():
    """Launch attempt after GRANNY_BUILDER_LAUNCH_RETRY_S must fire again."""
    import unseen_university.devices.granny.daemon as daemon
    daemon._last_launch_attempt["DickSimnel.0"] = time.time() - daemon.GRANNY_BUILDER_LAUNCH_RETRY_S - 1

    with patch("unseen_university.devices.granny.daemon.subprocess.Popen") as mock_popen:
        daemon._launch_builder("DickSimnel.0", {"launch_cmd": "echo retry"})

    mock_popen.assert_called_once()
