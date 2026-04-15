"""
test_request_compaction.py — T-compact-via-tmux-bug

Tests for igor_mcp._request_compaction's defensive improvements:
env-var check, session existence verification, and honest status
return.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab" / "claudecode"))

import igor_mcp  # noqa: E402

# ── env var missing ──────────────────────────────────────────────────────────


def test_missing_env_var_returns_error(monkeypatch):
    monkeypatch.delenv("CLAUDE_TMUX_SESSION", raising=False)
    result = igor_mcp._request_compaction("preserve this")
    assert "ERROR" in result
    assert "CLAUDE_TMUX_SESSION" in result


# ── session doesn't exist ────────────────────────────────────────────────────


def test_nonexistent_session_returns_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "nonexistent-session-xyz")

    mock_has_session = MagicMock()
    mock_has_session.returncode = 1  # non-zero = session doesn't exist

    with patch(
        "igor_mcp.subprocess.run",
        return_value=mock_has_session,
    ):
        result = igor_mcp._request_compaction("preserve this")
    assert "ERROR" in result
    assert "nonexistent-session-xyz" in result
    assert "does not exist" in result


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_sends_keys(monkeypatch):
    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "claude-test")

    call_log = []

    def _fake_run(args, **kwargs):
        call_log.append(args)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    with patch("igor_mcp.subprocess.run", side_effect=_fake_run):
        result = igor_mcp._request_compaction("preserve: session=x done")

    # Should have called has-session then send-keys
    assert len(call_log) == 2
    assert "has-session" in call_log[0]
    assert "send-keys" in call_log[1]

    # Result should include honest warning about TUI drop limitation
    assert "WARNING" in result or "warning" in result.lower()
    assert "claude-test" in result


# ── send-keys failure ────────────────────────────────────────────────────────


def test_send_keys_failure_surfaces_error(monkeypatch):
    import subprocess as _sp

    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "claude-test")

    call_count = {"n": 0}

    def _fake_run(args, **kwargs):
        call_count["n"] += 1
        if "has-session" in args:
            r = MagicMock()
            r.returncode = 0
            return r
        # send-keys — raise CalledProcessError
        raise _sp.CalledProcessError(
            returncode=1,
            cmd=args,
            stderr="tmux: send-keys failed",
            output="",
        )

    with patch("igor_mcp.subprocess.run", side_effect=_fake_run):
        result = igor_mcp._request_compaction("preserve")
    assert "ERROR" in result
    assert "send-keys" in result


# ── tmux not found ───────────────────────────────────────────────────────────


def test_tmux_not_found_returns_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "claude-test")

    with patch(
        "igor_mcp.subprocess.run",
        side_effect=FileNotFoundError("tmux"),
    ):
        result = igor_mcp._request_compaction("preserve")
    assert "ERROR" in result
    assert "tmux not found" in result


# ── has-session timeout ──────────────────────────────────────────────────────


def test_has_session_timeout_returns_error(monkeypatch):
    import subprocess as _sp

    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "claude-test")

    with patch(
        "igor_mcp.subprocess.run",
        side_effect=_sp.TimeoutExpired(cmd="tmux", timeout=3),
    ):
        result = igor_mcp._request_compaction("preserve")
    assert "ERROR" in result
    assert "timeout" in result
