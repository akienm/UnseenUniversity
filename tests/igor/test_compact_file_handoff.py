"""
test_compact_file_handoff.py — T-compact-via-file-handoff (#464)

Tests for file-based compact handoff between /savestate and the hook.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab" / "claudecode"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cc_hook_pending import (  # noqa: E402
    COMPACT_MAX_AGE_SECS,
    COMPACT_PENDING_FILE,
    _check_compact_pending,
    write_compact_pending,
)


@pytest.fixture(autouse=True)
def _cleanup_compact_file():
    """Remove compact pending file before and after each test."""
    if COMPACT_PENDING_FILE.exists():
        COMPACT_PENDING_FILE.unlink()
    yield
    if COMPACT_PENDING_FILE.exists():
        COMPACT_PENDING_FILE.unlink()


class TestWriteCompactPending:
    def test_writes_file(self):
        result = write_compact_pending("preserve: session=test")
        assert "ERROR" not in result
        assert COMPACT_PENDING_FILE.exists()
        assert COMPACT_PENDING_FILE.read_text() == "preserve: session=test"

    def test_overwrites_existing(self):
        write_compact_pending("first")
        write_compact_pending("second")
        assert COMPACT_PENDING_FILE.read_text() == "second"


class TestCheckCompactPending:
    def test_returns_empty_when_no_file(self):
        assert _check_compact_pending() == ""

    def test_reads_and_deletes_file(self):
        COMPACT_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMPACT_PENDING_FILE.write_text("preserve: test string")
        result = _check_compact_pending()
        assert result == "preserve: test string"
        assert not COMPACT_PENDING_FILE.exists()

    def test_fires_exactly_once(self):
        COMPACT_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMPACT_PENDING_FILE.write_text("once only")
        first = _check_compact_pending()
        second = _check_compact_pending()
        assert first == "once only"
        assert second == ""

    def test_drops_stale_file(self):
        """T-cc-stale-compact-request-leak: file older than COMPACT_MAX_AGE_SECS
        is dropped without firing (prevents cross-session leaks)."""
        import os
        import time

        COMPACT_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMPACT_PENDING_FILE.write_text("preserve: session=2026-04-16a")
        # Backdate the file by (max_age + 60)s
        stale_ts = time.time() - (COMPACT_MAX_AGE_SECS + 60)
        os.utime(COMPACT_PENDING_FILE, (stale_ts, stale_ts))

        result = _check_compact_pending()
        assert result == ""  # no preserve string returned
        assert not COMPACT_PENDING_FILE.exists()  # file removed


class TestHookIntegration:
    def test_hook_injects_compact_context(self):
        from cc_hook_pending import main

        COMPACT_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        COMPACT_PENDING_FILE.write_text("preserve: compact test")

        hook_input = json.dumps({"session_id": "test-session"})
        import io

        captured = io.StringIO()
        with patch("sys.stdin", io.StringIO(hook_input)):
            with patch("sys.stdout", captured):
                with patch("cc_hook_pending.fetch_new_messages", return_value=([], "")):
                    main()

        output = json.loads(captured.getvalue())
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "COMPACT REQUESTED" in ctx
        assert "preserve: compact test" in ctx

    def test_hook_returns_empty_when_no_compact(self):
        from cc_hook_pending import main

        hook_input = json.dumps({"session_id": "test-session"})
        import io

        captured = io.StringIO()
        with patch("sys.stdin", io.StringIO(hook_input)):
            with patch("sys.stdout", captured):
                with patch("cc_hook_pending.fetch_new_messages", return_value=([], "")):
                    main()

        output = json.loads(captured.getvalue())
        assert output == {}
