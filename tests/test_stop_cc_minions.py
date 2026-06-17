"""Tests for lab/claudecode/stop_cc_minions.py."""

from __future__ import annotations

import signal
from unittest.mock import MagicMock, call, patch

import pytest

import devlab.claudecode.stop_cc_minions as sut


def _run_result(stdout=""):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


class TestListCcSessions:
    def test_returns_cc_prefixed_sessions(self):
        lines = "cc-T-foo\nclaude-main\ngranny\ncc-T-bar\nweb-server"
        with patch("subprocess.run", return_value=_run_result(lines)):
            result = sut._list_cc_sessions()
        assert result == ["cc-T-foo", "cc-T-bar"]

    def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("tmux gone")):
            result = sut._list_cc_sessions()
        assert result == []


class TestListSprintPids:
    def test_finds_sprint_pids(self):
        ps_out = (
            "akien  1234  0.0  0.0 /bin/bash\n"
            "akien  5678  1.0  0.0 claude --dangerously-skip-permissions -p /sprint-ticket T-foo\n"
            "akien  9999  0.5  0.0 grep sprint-ticket\n"
        )
        with patch("subprocess.run", return_value=_run_result(ps_out)):
            result = sut._list_sprint_pids()
        assert result == [5678]

    def test_excludes_grep_process(self):
        ps_out = "akien  9999  0.5  0.0 grep sprint-ticket\n"
        with patch("subprocess.run", return_value=_run_result(ps_out)):
            result = sut._list_sprint_pids()
        assert result == []


class TestKillSessions:
    def test_kills_cc_sessions(self):
        with patch("subprocess.run", return_value=_run_result()) as mock_run:
            killed = sut._kill_sessions(
                ["cc-T-foo", "cc-T-bar"], dry_run=False, quiet=True
            )
        assert killed == 2
        kill_calls = [c for c in mock_run.call_args_list if "kill-session" in str(c)]
        assert len(kill_calls) == 2

    def test_skips_protected_sessions(self):
        with patch("subprocess.run", return_value=_run_result()) as mock_run:
            killed = sut._kill_sessions(
                ["claude-main", "granny", "cc-T-foo"], dry_run=False, quiet=True
            )
        assert killed == 1
        kill_calls = [c for c in mock_run.call_args_list if "kill-session" in str(c)]
        assert len(kill_calls) == 1

    def test_dry_run_kills_nothing(self):
        with patch("subprocess.run", return_value=_run_result()) as mock_run:
            killed = sut._kill_sessions(["cc-T-foo"], dry_run=True, quiet=True)
        assert killed == 0  # dry-run doesn't count as a kill
        kill_calls = [c for c in mock_run.call_args_list if "kill-session" in str(c)]
        assert len(kill_calls) == 0


class TestKillPids:
    def test_sends_sigterm(self):
        with patch("os.kill") as mock_kill:
            killed = sut._kill_pids([1234, 5678], dry_run=False, quiet=True)
        assert killed == 2
        mock_kill.assert_any_call(1234, signal.SIGTERM)
        mock_kill.assert_any_call(5678, signal.SIGTERM)

    def test_tolerates_process_not_found(self):
        with patch("os.kill", side_effect=ProcessLookupError):
            killed = sut._kill_pids([9999], dry_run=False, quiet=True)
        assert killed == 0


class TestMain:
    def test_nothing_to_kill_returns_zero(self):
        with (
            patch("sys.argv", ["stop_cc_minions.py"]),
            patch.object(sut, "_list_cc_sessions", return_value=[]),
            patch.object(sut, "_list_sprint_pids", return_value=[]),
        ):
            rc = sut.main()
        assert rc == 0

    def test_kills_sessions_and_pids(self):
        posted = []
        with (
            patch("sys.argv", ["stop_cc_minions.py"]),
            patch.object(sut, "_list_cc_sessions", return_value=["cc-T-foo"]),
            patch.object(
                sut,
                "_list_sprint_pids",
                side_effect=[
                    [1234],  # first call (before kill)
                    [],  # second call (after SIGKILL straggler check)
                ],
            ),
            patch.object(sut, "_kill_sessions", return_value=1),
            patch.object(sut, "_kill_pids", return_value=1),
            patch.object(sut, "_verify_clear", return_value=True),
            patch.object(sut, "_post_channel", side_effect=posted.append),
        ):
            rc = sut.main()
        assert rc == 0
        assert any("CC_MINIONS_STOPPED" in m for m in posted)

    def test_posts_timeout_when_not_clear(self):
        posted = []
        with (
            patch("sys.argv", ["stop_cc_minions.py"]),
            patch.object(sut, "_list_cc_sessions", return_value=["cc-T-foo"]),
            patch.object(sut, "_list_sprint_pids", return_value=[]),
            patch.object(sut, "_kill_sessions", return_value=1),
            patch.object(sut, "_kill_pids", return_value=0),
            patch.object(sut, "_verify_clear", return_value=False),
            patch.object(sut, "_post_channel", side_effect=posted.append),
        ):
            rc = sut.main()
        assert rc == 1
        assert any("timeout" in m for m in posted)
