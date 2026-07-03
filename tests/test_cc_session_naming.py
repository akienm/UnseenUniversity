"""Tests for CC session name derivation — T-cc-session-hostname-naming.

Verifies that _detect_session_name() returns the correct <hostname>_cc_N
based on existing tmux sessions, and that CC_TMUX_SESSION env var wins.

Fakes use underscore session names because that is what tmux actually stores
(it converts '.' -> '_'); the old dotted fakes were a fiction that masked
T-cc-tmux-session-dot-naming-broken.
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
        cc_tmux_session="akiendelllinux_cc_0",
        tmux_sessions=["akiendelllinux_cc_0", "akiendelllinux_cc_1"],
    )
    assert mod.TMUX_SESSION == "akiendelllinux_cc_0"
    # subprocess.run should NOT be called when env var is present
    mock_run.assert_not_called()


def test_env_var_arbitrary_value(monkeypatch):
    """Any value in CC_TMUX_SESSION is used as-is."""
    mod, _ = _reload_constants(monkeypatch, cc_tmux_session="custom-session-name")
    assert mod.TMUX_SESSION == "custom-session-name"


# ── Auto-detection: no existing sessions ─────────────────────────────────────

def test_no_existing_sessions_returns_cc0(monkeypatch):
    """With no existing tmux sessions, returns <hostname>_cc_0."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "testhost_cc_0"


def test_cc0_exists_returns_cc1(monkeypatch):
    """When <hostname>_cc_0 is taken, returns <hostname>_cc_1."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=["testhost_cc_0"])
    assert mod.TMUX_SESSION == "testhost_cc_1"


def test_cc0_and_cc1_exist_returns_cc2(monkeypatch):
    """When _cc_0 and _cc_1 are taken, returns _cc_2."""
    with patch("socket.gethostname", return_value="testhost"):
        mod, _ = _reload_constants(
            monkeypatch,
            tmux_sessions=["testhost_cc_0", "testhost_cc_1", "granny", "igor"],
        )
    assert mod.TMUX_SESSION == "testhost_cc_2"


# ── Auto-detection: tmux unavailable ─────────────────────────────────────────

def test_tmux_error_falls_back_to_cc0(monkeypatch):
    """When tmux list-sessions fails, fall back to <hostname>_cc_0."""
    from pathlib import Path
    _missing = Path("/nonexistent/cc_session_test_stub.txt")
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")), \
         patch("unseen_university.devices.claude.constants.cc_session_path", return_value=_missing):
        import unseen_university.devices.claude.constants as mod
        importlib.reload(mod)
    assert mod.TMUX_SESSION == "testhost_cc_0"


def test_tmux_timeout_falls_back_to_cc0(monkeypatch):
    """When tmux list-sessions times out, fall back to <hostname>_cc_0."""
    from pathlib import Path
    _missing = Path("/nonexistent/cc_session_test_stub.txt")
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 2)), \
         patch("unseen_university.devices.claude.constants.cc_session_path", return_value=_missing):
        import unseen_university.devices.claude.constants as mod
        importlib.reload(mod)
    assert mod.TMUX_SESSION == "testhost_cc_0"


# ── Hostname: domain stripped ─────────────────────────────────────────────────

def test_fqdn_hostname_stripped_to_short(monkeypatch):
    """FQDN hostnames like 'host.local.example.com' are trimmed to 'host'."""
    with patch("socket.gethostname", return_value="myhost.local.example.com"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "myhost_cc_0"


def test_hostname_lowercased(monkeypatch):
    """Uppercase hostname is lowercased in the session name."""
    with patch("socket.gethostname", return_value="UPPERCASE"):
        mod, _ = _reload_constants(monkeypatch, tmux_sessions=[])
    assert mod.TMUX_SESSION == "uppercase_cc_0"


# ── Dot-free tmux naming (T-cc-tmux-session-dot-naming-broken) ────────────────
# Hermetic: calls the derivation/scan functions directly (no importlib.reload,
# no dependency on a real cc_session.txt) so it is immune to the reload-clobbers-
# patch harness fragility that the reload-based tests above suffer under a live
# session. This is the proof node: it is GREEN on the underscore code and RED on
# the old dotted code, because a dotted name can never match/increment past a
# real (underscore-stored) tmux session.

def test_detect_increments_past_real_underscore_session():
    """_detect_session_name must SEE an existing underscore session and increment.

    The old dotted derivation returned 'testhost.cc.0', which is not present in
    the (real, underscore) session set, so it collided on slot 0 instead of
    advancing — the core bug.
    """
    import unseen_university.devices.claude.constants as m
    fake = MagicMock(stdout="testhost_cc_0\nother\n", returncode=0)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", lambda *a, **kw: fake):
        got = m._detect_session_name()
    assert got == "testhost_cc_1"
    assert "." not in got  # dot-free: tmux would mangle a dotted name


def test_find_existing_detects_underscore_session():
    """_find_existing_cc_session must match the underscore names tmux stores."""
    import unseen_university.devices.claude.constants as m
    fake = MagicMock(stdout="unrelated\ntesthost_cc_2\n", returncode=0)
    with patch("socket.gethostname", return_value="testhost"), \
         patch("subprocess.run", lambda *a, **kw: fake):
        assert m._find_existing_cc_session() == "testhost_cc_2"
