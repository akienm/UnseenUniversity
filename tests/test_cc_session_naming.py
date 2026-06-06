"""Tests for CC session name derivation — T-cc-session-hostname-naming.

Verifies that _detect_session_name() returns the correct <hostname>.cc.N
based on existing tmux sessions, and that CC_TMUX_SESSION env var wins.
"""

from __future__ import annotations

import importlib
import subprocess
from unittest.mock import MagicMock, patch


def _reload_constants(monkeypatch, cc_tmux_session=None, tmux_sessions=None):
    """Reload constants module with controlled env and tmux output."""
    if cc_tmux_session is not None:
        monkeypatch.setenv("CC_TMUX_SESSION", cc_tmux_session)
    else:
        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)

    fake_sessions = "\n".join(tmux_sessions or [])
    mock_result = MagicMock()
    mock_result.stdout = fake_sessions
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        import devices.claude.constants as mod
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
    with patch("socket.gethostname", return_value="testhost"):
        with patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")):
            import devices.claude.constants as mod
            importlib.reload(mod)
    assert mod.TMUX_SESSION == "testhost.cc.0"


def test_tmux_timeout_falls_back_to_cc0(monkeypatch):
    """When tmux list-sessions times out, fall back to <hostname>.cc.0."""
    with patch("socket.gethostname", return_value="testhost"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 2)):
            import devices.claude.constants as mod
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
