"""
tests/test_debug_session.py — DESIGNED:T-mcp-igor-cognition-debug-capability

Tests for debug_session.py: claim/status/release/query API.
"""

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect instance dir to tmp_path so tests don't touch live state."""
    import devices.igor.cognition.debug_session as _ds

    fake_instance = tmp_path / "instance"
    fake_instance.mkdir()

    class FakePaths:
        instance = fake_instance

    monkeypatch.setattr(_ds, "_paths", lambda: FakePaths())
    yield fake_instance


def test_claim_returns_handle(clean_state):
    from devices.igor.cognition.debug_session import claim

    handle = claim("test")
    assert handle.startswith("dbg-")
    assert len(handle) == 12  # "dbg-" + 8 hex chars


def test_claim_writes_flag(clean_state):
    from devices.igor.cognition.debug_session import claim

    claim("session")
    assert (clean_state / "debug_session.flag").exists()


def test_status_active_after_claim(clean_state):
    from devices.igor.cognition.debug_session import claim, status

    handle = claim("session")
    s = status(handle)
    assert s["active"] is True
    assert s["handle"] == handle
    assert s["scope"] == "session"


def test_status_inactive_before_claim(clean_state):
    from devices.igor.cognition.debug_session import status

    s = status()
    assert s["active"] is False


def test_status_handle_mismatch(clean_state):
    from devices.igor.cognition.debug_session import claim, status

    claim("session")
    s = status("dbg-wronghan")
    assert s["active"] is False
    assert "error" in s


def test_release_clears_flag(clean_state):
    from devices.igor.cognition.debug_session import claim, release

    handle = claim("session")
    result = release(handle)
    assert result is True
    assert not (clean_state / "debug_session.flag").exists()
    assert not (clean_state / "debug_session_state.json").exists()


def test_release_no_session(clean_state):
    from devices.igor.cognition.debug_session import release

    result = release()
    assert result is False


def test_query_returns_log_lines(clean_state):
    from devices.igor.cognition.debug_session import claim, log_line, query

    handle = claim("session")
    log_line(handle, "phase=thalamus intent=factual_question")
    log_line(handle, "phase=gateway is_user_turn=True tier=cloud/interactive")
    lines = query(handle)
    assert len(lines) == 2
    assert "thalamus" in lines[0]
    assert "gateway" in lines[1]


def test_query_respects_limit(clean_state):
    from devices.igor.cognition.debug_session import claim, log_line, query

    handle = claim("session")
    for i in range(10):
        log_line(handle, f"line {i}")
    lines = query(handle, limit=3)
    assert len(lines) == 3
    assert lines[-1] == "line 9"


def test_full_lifecycle(clean_state):
    """claim → status active → release → status inactive."""
    from devices.igor.cognition.debug_session import claim, release, status

    handle = claim("session")
    assert status(handle)["active"] is True
    release(handle)
    assert status(handle)["active"] is False
