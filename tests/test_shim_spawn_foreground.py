"""Tests for BaseShim.spawn_foreground_session (T-shim-foreground-spawn).

Covers all four branches:
  - new session + tty (attach=True): os.execvp called with new-session args
  - new session + no tty (attach=False): subprocess detach path
  - new session + no_attach=True: subprocess detach path regardless of tty
  - existing session + tty: os.execvp called with attach-session args
  - existing session + no tty: silent return, no subprocess calls
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.shim import BaseShim


def _make_run(session_exists: bool):
    """Return a mock subprocess.run that gates has-session on session_exists."""

    def _run(args, **kwargs):
        if len(args) > 1 and args[1] == "has-session":
            return MagicMock(returncode=0 if session_exists else 1)
        return MagicMock(returncode=0)

    return _run


# ── New session — attach path (tty present) ───────────────────────────────────


def test_new_session_with_tty_execs_new_session(monkeypatch):
    """tty + no_attach=False + no session → os.execvp with new-session."""
    exec_calls = []
    monkeypatch.setattr("unseen_university.shim.subprocess.run", _make_run(False))
    monkeypatch.setattr("unseen_university.shim.os.execvp", lambda p, a: exec_calls.append((p, a)))

    import sys as _sys
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

    BaseShim.spawn_foreground_session("test-session", ["bash", "/path/daemon.sh"])

    assert len(exec_calls) == 1
    prog, args = exec_calls[0]
    assert prog == "tmux"
    assert "new-session" in args
    assert "-s" in args
    assert "test-session" in args
    assert "bash" in args
    assert "/path/daemon.sh" in args


# ── New session — detached path (no tty) ─────────────────────────────────────


def test_new_session_no_tty_creates_detached(monkeypatch):
    """no tty + no_attach=False → detached new-session + send-keys."""
    run_calls = []

    def _run(args, **kw):
        run_calls.append(list(args))
        if len(args) > 1 and args[1] == "has-session":
            return MagicMock(returncode=1)
        return MagicMock(returncode=0)

    monkeypatch.setattr("unseen_university.shim.subprocess.run", _run)

    import sys as _sys
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

    BaseShim.spawn_foreground_session("test-session", ["bash", "/path/daemon.sh"])

    new_session_call = next(c for c in run_calls if "new-session" in c)
    assert "-d" in new_session_call

    send_keys_call = next(c for c in run_calls if "send-keys" in c)
    assert "bash /path/daemon.sh" in send_keys_call


def test_new_session_no_attach_flag_creates_detached(monkeypatch):
    """no_attach=True → detached path regardless of tty."""
    run_calls = []

    def _run(args, **kw):
        run_calls.append(list(args))
        if len(args) > 1 and args[1] == "has-session":
            return MagicMock(returncode=1)
        return MagicMock(returncode=0)

    monkeypatch.setattr("unseen_university.shim.subprocess.run", _run)

    import sys as _sys
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)  # tty present, but no_attach wins

    BaseShim.spawn_foreground_session("test-session", ["bash", "/path/daemon.sh"], no_attach=True)

    new_session_call = next(c for c in run_calls if "new-session" in c)
    assert "-d" in new_session_call


# ── Session already exists — attach path ─────────────────────────────────────


def test_existing_session_with_tty_execs_attach(monkeypatch):
    """session exists + tty → os.execvp with attach-session."""
    exec_calls = []
    monkeypatch.setattr("unseen_university.shim.subprocess.run", _make_run(True))
    monkeypatch.setattr("unseen_university.shim.os.execvp", lambda p, a: exec_calls.append((p, a)))

    import sys as _sys
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

    BaseShim.spawn_foreground_session("test-session", ["bash", "/path/daemon.sh"])

    assert len(exec_calls) == 1
    prog, args = exec_calls[0]
    assert prog == "tmux"
    assert "attach-session" in args
    assert "test-session" in args


# ── Session already exists — no tty ──────────────────────────────────────────


def test_existing_session_no_tty_returns_silently(monkeypatch):
    """session exists + no tty → silent return, no extra subprocess calls."""
    run_calls = []

    def _run(args, **kw):
        run_calls.append(list(args))
        return MagicMock(returncode=0)  # has-session succeeds → exists

    monkeypatch.setattr("unseen_university.shim.subprocess.run", _run)

    import sys as _sys
    monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

    BaseShim.spawn_foreground_session("test-session", ["bash", "/path/daemon.sh"])

    # Only the has-session probe should have run — no new-session or send-keys
    assert all("has-session" in c for c in run_calls), (
        f"unexpected subprocess calls beyond has-session: {run_calls}"
    )
