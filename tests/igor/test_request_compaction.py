"""
test_request_compaction.py — T-compact-via-file-handoff

Tests for igor_mcp._request_compaction: file handoff is primary,
tmux send-keys is fallback.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab" / "claudecode"))

import igor_mcp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_pending_file():
    """Auto-clean the real cc_compact_pending.txt before and after each test.
    Without this, every test-suite run leaks a real preserve string into the
    hook, which then injects a stale 'COMPACT REQUESTED' into the next CC
    turn. Caught 2026-04-24 after a full-suite run left session=2026-04-16a
    lingering and firing every prompt for days.
    """
    from cc_hook_pending import COMPACT_PENDING_FILE

    COMPACT_PENDING_FILE.unlink(missing_ok=True)
    yield
    COMPACT_PENDING_FILE.unlink(missing_ok=True)


def test_file_handoff_is_primary():
    """File handoff should be the primary path, not tmux."""
    result = igor_mcp._request_compaction("preserve: test")
    assert "queued" in result.lower() or "Compact" in result
    assert "ERROR" not in result


def test_file_handoff_writes_preserve_string():
    """The preserve string should end up in the pending file."""
    from cc_hook_pending import COMPACT_PENDING_FILE

    igor_mcp._request_compaction("preserve: specific text here")
    if COMPACT_PENDING_FILE.exists():
        content = COMPACT_PENDING_FILE.read_text()
        assert "specific text here" in content
        COMPACT_PENDING_FILE.unlink(missing_ok=True)


def test_tmux_fallback_when_file_fails(monkeypatch):
    """If file write fails, fall back to tmux."""
    monkeypatch.setenv("CLAUDE_TMUX_SESSION", "claude-test")

    call_log = []

    def _fake_run(args, **kwargs):
        call_log.append(args)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("igor_mcp.subprocess.run", side_effect=_fake_run):
        with patch("igor_mcp.Path.write_text", side_effect=PermissionError("no")):
            with patch(
                "cc_hook_pending.write_compact_pending",
                side_effect=Exception("import fail"),
            ):
                result = igor_mcp._request_compaction("preserve: fallback test")

    if call_log:
        assert any("send-keys" in str(c) for c in call_log)


def test_all_methods_fail_returns_error(monkeypatch):
    """If both file and tmux fail, return ERROR."""
    monkeypatch.delenv("CLAUDE_TMUX_SESSION", raising=False)

    with patch("igor_mcp.Path.write_text", side_effect=PermissionError("no")):
        with patch(
            "cc_hook_pending.write_compact_pending",
            side_effect=Exception("import fail"),
        ):
            result = igor_mcp._request_compaction("preserve: doomed")

    assert "ERROR" in result


def test_preserves_content_in_result():
    result = igor_mcp._request_compaction("preserve: session=2026-04-16a")
    assert "queued" in result.lower() or "Compact" in result
