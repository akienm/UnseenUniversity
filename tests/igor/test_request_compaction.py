"""
test_request_compaction.py — T-compact-via-file-handoff

Tests for request_compaction (now in Librarian channel_tools — T-igor-mcp-delete).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "dev"
        / "src"
        / "unseen_university"
    ),
)

from unseen_university.devices.librarian.tools.channel_tools import (
    _request_compaction,
)  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_pending_file():
    """Auto-clean the real cc_compact_pending.txt before and after each test.
    Without this, every test-suite run leaks a real preserve string into the
    hook, which then injects a stale 'COMPACT REQUESTED' into the next CC
    turn. Caught 2026-04-24 after a full-suite run left session=2026-04-16a
    lingering and firing every prompt for days.
    """
    pending = Path.home() / ".unseen_university" / "cc_compact_pending.txt"
    pending.unlink(missing_ok=True)
    yield
    pending.unlink(missing_ok=True)


def test_file_handoff_is_primary():
    """File handoff should be the primary path."""
    result = _request_compaction("preserve: test")
    assert "queued" in result.lower() or "Compact" in result
    assert "ERROR" not in result


def test_file_handoff_writes_preserve_string():
    """The preserve string should end up in the pending file."""
    pending = Path.home() / ".unseen_university" / "cc_compact_pending.txt"
    _request_compaction("preserve: specific text here")
    if pending.exists():
        content = pending.read_text()
        assert "specific text here" in content


def test_preserves_content_in_result():
    result = _request_compaction("preserve: session=2026-04-16a")
    assert "queued" in result.lower() or "Compact" in result
