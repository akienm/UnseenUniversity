"""
test_rotate_igor_console.py — T-igor-console-midnight-rotate

Tests for the Igor console pipe-pane rotation script.
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.claudecode.rotate_igor_console import (  # noqa: E402
    rotate,
    target_log_path,
    tmux_session_exists,
)


class TestTargetLogPath:
    def test_uses_yyyymmdd_filename(self, tmp_path):
        d = datetime(2026, 4, 29, 12, 0, 0)
        out = target_log_path(d, adc_home=tmp_path)
        assert out.name == "20260429.console.log"

    def test_lands_in_igor_wild_dir(self, tmp_path):
        d = datetime(2026, 4, 29)
        out = target_log_path(d, adc_home=tmp_path)
        assert "Igor-wild-0001" in str(out)
        assert out.parent == tmp_path / "logs" / "Igor-wild-0001"

    def test_handles_year_boundary(self, tmp_path):
        d = datetime(2026, 12, 31, 23, 59)
        out = target_log_path(d, adc_home=tmp_path)
        assert out.name == "20261231.console.log"


class TestTmuxSessionExists:
    def test_returns_false_when_tmux_missing(self):
        with patch(
            "lab.claudecode.rotate_igor_console.shutil.which", return_value=None
        ):
            assert tmux_session_exists("igor") is False

    def test_returns_true_when_session_present(self):
        with patch(
            "lab.claudecode.rotate_igor_console.shutil.which",
            return_value="/usr/bin/tmux",
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert tmux_session_exists("igor") is True

    def test_returns_false_when_session_missing(self):
        with patch(
            "lab.claudecode.rotate_igor_console.shutil.which",
            return_value="/usr/bin/tmux",
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert tmux_session_exists("igor") is False


class TestRotate:
    def test_no_session_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=False,
        ):
            assert rotate() == 1

    def test_dry_run_skips_tmux_calls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=True,
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            assert rotate(dry_run=True) == 0
        mock_run.assert_not_called()

    def test_success_calls_pipe_pane_twice(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=True,
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            now = datetime(2026, 4, 29, 0, 1)
            rc = rotate(now=now)
        assert rc == 0
        # First call: close (no -o flag); second: open with -o
        assert mock_run.call_count == 2
        first_args = mock_run.call_args_list[0].args[0]
        second_args = mock_run.call_args_list[1].args[0]
        assert "-o" not in first_args
        assert "-o" in second_args
        # The pipe target path should contain today's date
        pipe_cmd = second_args[-1]
        assert "20260429.console.log" in pipe_cmd

    def test_close_failure_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=True,
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="boom")
            assert rotate() == 2

    def test_creates_log_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        log_dir = tmp_path / "logs" / "Igor-wild-0001"
        assert not log_dir.exists()
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=True,
        ), patch("lab.claudecode.rotate_igor_console.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            rotate(now=datetime(2026, 4, 29))
        assert log_dir.is_dir()

    def test_session_name_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_DATACENTER_HOME", str(tmp_path))
        monkeypatch.setenv("IGOR_TMUX_SESSION", "myigor")
        with patch(
            "lab.claudecode.rotate_igor_console.tmux_session_exists",
            return_value=True,
        ) as mock_exists, patch(
            "lab.claudecode.rotate_igor_console.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            rotate()
        mock_exists.assert_called_once_with("myigor")
        # Both tmux calls target the named session
        for call in mock_run.call_args_list:
            assert "myigor" in call.args[0]
