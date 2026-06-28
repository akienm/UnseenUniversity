"""Tests for CC session name derivation — T-cc-session-hostname-naming.

Verifies that _detect_session_name() returns the correct <hostname>.cc.N
based on existing tmux sessions, and that CC_TMUX_SESSION env var wins.
"""

from __future__ import annotations

import importlib
import subprocess
from unittest.mock import MagicMock, patch


def _make_run_mock(tmux_sessions):
    """Return a subprocess.run mock.

    First call (from _find_existing_cc_session): returns empty stdout so no
    existing session is found and _resolve_session_name falls through to
    _detect_session_name.
    Second call (from _detect_session_name): returns the desired session list
    so slot-detection picks the correct N.
    Subsequent calls: repeat the detect-phase result.
    """
    empty = MagicMock()
    empty.stdout = ""
    empty.returncode = 0

    detect = MagicMock()
    detect.stdout = "\n".join(tmux_sessions or [])
    detect.returncode = 0

    mock = MagicMock(side_effect=[empty, detect] + [detect] * 20)
    return mock


def _reload_constants(monkeypatch, cc_tmux_session=None, tmux_sessions=None):
    """Reload constants module with controlled env and tmux output.

    When cc_tmux_session is None (auto-detection tests), subprocess.run is
    mocked so that _find_existing_cc_session sees no sessions (falls through)
    and _detect_session_name sees the desired tmux_sessions list.
    cc_session.txt is suppressed via a nonexistent path.
    """
    from pathlib import Path

    if cc_tmux_session is not None:
        monkeypatch.setenv("CC_TMUX_SESSION", cc_tmux_session)
    else:
        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)

    mock_run = _make_run_mock(tmux_sessions)

    # Nonexistent path so read_text raises FileNotFoundError naturally.
    _missing = Path("/nonexistent/cc_session_test_stub.txt")

    with patch("subprocess.run", mock_run), \
         patch("unseen_university.devices.claude.constants.cc_session_path", return_value=_missing):
        import unseen_university.devices.claude.constants as mod
        importlib.reload(mod)
        return mod, mock_run


# ── CC_TMUX_SESSION env var overrides everything ──────────────────────────────

def test_env_var_overrides_detection(monkeypatch):
    mod, mock_run = _reload_constants(
        monkeypatch,
        cc_tmux_session="akiendelllinux.cc.0",
        tmux_sessions=["akiendelllinux.cc.0", "akiendelllinux.cc.1"],
    )
    assert mod.TMUX_SESSION == "akiendelllinux.cc.0"
    # subprocess.run should NOT be called when env var is present
    mock_run.assert_not_called()


def test_env_var_arbitrary_value(monkeypatch):
    """Any value in CC_TMUX_SESSION is used as-is."""
    mod, _ = _reload_constants(monkeypatch, cc_tmux_session="custom-session-name")
    assert mod.TMUX_SESSION == "custom-session-name"


# ── Auto-detection: no existing sessions ─────────────────────────────────────

def test_no_existing_sessions_returns_cc0(monkeypatch):
    """With no existing tmux sessions, returns <hostname>.cc.0."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "testhost.cc.0"


def test_cc0_exists_returns_cc1(monkeypatch):
    """When <hostname>.cc.0 is taken, returns <hostname>.cc.1."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=["testhost.cc.0"])
    assert mod.TMUX_SESSION == "testhost.cc.1"


def test_cc0_and_cc1_exist_returns_cc2(monkeypatch):
    """When .cc.0 and .cc.1 are taken, returns .cc.2."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(
            monkeypatch,
            tmux_sessions=["testhost.cc.0", "testhost.cc.1", "granny", "igor"],
        )
    assert mod.TMUX_SESSION == "testhost.cc.2"


# ── Auto-detection: tmux unavailable ─────────────────────────────────────────

def test_tmux_error_falls_back_to_cc0(monkeypatch):
    """When tmux list-sessions fails, fall back to <hostname>.cc.0."""
    from pathlib import Path
    _missing = Path("/nonexistent/cc_session_test_stub.txt")
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")), \
         patch("unseen_university.devices.claude.constants.cc_session_path", return_value=_missing):
        import unseen_university.devices.claude.constants as mod
        importlib.reload(mod)
    assert mod.TMUX_SESSION == "testhost.cc.0"


def test_tmux_timeout_falls_back_to_cc0(monkeypatch):
    """When tmux list-sessions times out, fall back to <hostname>.cc.0."""
    from pathlib import Path
    _missing = Path("/nonexistent/cc_session_test_stub.txt")
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 2)), \
         patch("unseen_university.devices.claude.constants.cc_session_path", return_value=_missing):
        import unseen_university.devices.claude.constants as mod
        importlib.reload(mod)
    assert mod.TMUX_SESSION == "testhost.cc.0"


# ── Hostname: domain stripped ─────────────────────────────────────────────────

def test_fqdn_hostname_stripped_to_short(monkeypatch):
    """FQDN hostnames like 'host.local.example.com' are trimmed to 'host'."""
    with patch("socket.gethostname", return_value="myhost.local.example.com"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "myhost.cc.0"


def test_hostname_lowercased(monkeypatch):
    """Uppercase hostname is lowercased in the session name."""
    with patch("socket.gethostname", return_value="UPPERCASE"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "uppercase.cc.0"
