"""
Tests for CCWorkerListener ack note + nag loop (T-cc-shim-ack-nag).

Verifies:
- dispatch envelope → ack sent to Granny mailbox
- ticket note appended with ack timestamp
- nag fires at interval when ticket still not in_progress
- nag stops when ticket moves to in_progress
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from devices.granny.cc_worker_listener import (
    CCWorkerListener,
    _NAG_TERMINAL_STATUSES,
)


def _make_dispatch_env(ticket_id: str, from_device: str = "granny.0"):
    return SimpleNamespace(
        from_device=from_device,
        payload={"kind": "dispatch", "ticket_id": ticket_id},
    )


def _make_listener(imap=None, nag_state_dir: Path | None = None, nag_interval: int = 1):
    """Build a CCWorkerListener with fast nag interval for tests."""
    listener = CCWorkerListener(
        imap=imap,
        cc_mailbox="cc.0",
        granny_mailbox="granny.0",
        tmux_session="test.cc.0",
        poll_interval=0.1,
    )
    # Patch nag interval and state dir
    import devices.granny.cc_worker_listener as mod
    mod._NAG_INTERVAL_S = nag_interval
    if nag_state_dir:
        mod._NAG_STATE_DIR = nag_state_dir
    return listener


class TestAckNote:
    def test_ack_note_appended_on_dispatch(self, tmp_path):
        """After receiving a dispatch, ack note is added to the ticket."""
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return MagicMock(returncode=0, stdout=json.dumps({"status": "sprint"}))

        env = _make_dispatch_env("T-test-001")
        listener = _make_listener(imap=None)

        with patch("subprocess.run", side_effect=fake_run):
            listener._add_ack_note("T-test-001")

        append_note_calls = [c for c in calls if "append-note" in c]
        assert len(append_note_calls) == 1
        note_args = append_note_calls[0]
        assert note_args[3] == "T-test-001"
        assert "CC.0 acked at" in note_args[4]

    def test_ack_note_contains_iso_timestamp(self, tmp_path):
        """Note text contains an ISO-8601 UTC timestamp."""
        captured = []

        def fake_run(args, **kw):
            captured.append(args)
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None)
        with patch("subprocess.run", side_effect=fake_run):
            listener._add_ack_note("T-test-002")

        note = captured[0][4]
        # e.g. "CC.0 acked at 2026-06-06T21:00:00Z"
        assert "T" in note and "Z" in note


class TestNagLoop:
    def test_nag_fires_when_ticket_still_sprint(self, tmp_path):
        """Nag sends tmux message when ticket stays in sprint status."""
        import devices.granny.cc_worker_listener as mod
        mod._NAG_STATE_DIR = tmp_path / "nag_state"
        mod._NAG_INTERVAL_S = 1

        nag_calls = []
        statuses = ["sprint", "sprint", "in_progress"]
        status_iter = iter(statuses)

        def fake_run(args, **kw):
            cmd = list(args)
            if "show" in cmd:
                st = next(status_iter, "in_progress")
                return MagicMock(returncode=0, stdout=json.dumps({"status": st}))
            if "send-keys" in cmd:
                nag_calls.append(args)
            if "append-note" in cmd:
                pass
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None, nag_state_dir=tmp_path / "nag_state", nag_interval=1)
        state_file = (tmp_path / "nag_state")
        state_file.mkdir(parents=True, exist_ok=True)
        state_file = state_file / "T-test-003.nag"
        state_file.write_text(json.dumps({"ticket_id": "T-test-003", "session": "test.cc.0"}))

        with patch("subprocess.run", side_effect=fake_run):
            t = threading.Thread(
                target=listener._nag_loop,
                args=("T-test-003", state_file),
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        # At least one nag was sent before status became in_progress
        nag_sends = [c for c in nag_calls if "send-keys" in list(c) and "check messages" in str(c)]
        assert len(nag_sends) >= 1

    def test_nag_stops_when_in_progress(self, tmp_path):
        """Nag loop exits cleanly when ticket reaches in_progress."""
        import devices.granny.cc_worker_listener as mod
        mod._NAG_STATE_DIR = tmp_path / "nag_state"
        mod._NAG_INTERVAL_S = 1
        (tmp_path / "nag_state").mkdir(parents=True, exist_ok=True)

        def fake_run(args, **kw):
            if "show" in list(args):
                return MagicMock(returncode=0, stdout=json.dumps({"status": "in_progress"}))
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None, nag_state_dir=tmp_path / "nag_state", nag_interval=1)
        state_file = tmp_path / "nag_state" / "T-test-004.nag"
        state_file.write_text(json.dumps({"ticket_id": "T-test-004", "session": "test.cc.0"}))

        with patch("subprocess.run", side_effect=fake_run):
            t = threading.Thread(
                target=listener._nag_loop,
                args=("T-test-004", state_file),
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        assert t.is_alive() is False
        assert not state_file.exists()

    def test_nag_state_file_cleaned_on_terminal(self, tmp_path):
        """Nag state file is removed when ticket reaches terminal status."""
        import devices.granny.cc_worker_listener as mod
        mod._NAG_STATE_DIR = tmp_path / "nag_state"
        mod._NAG_INTERVAL_S = 1
        (tmp_path / "nag_state").mkdir(parents=True, exist_ok=True)

        def fake_run(args, **kw):
            if "show" in list(args):
                return MagicMock(returncode=0, stdout=json.dumps({"status": "closed"}))
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None, nag_state_dir=tmp_path / "nag_state", nag_interval=1)
        state_file = tmp_path / "nag_state" / "T-test-005.nag"
        state_file.write_text(json.dumps({"ticket_id": "T-test-005", "session": "test.cc.0"}))

        with patch("subprocess.run", side_effect=fake_run):
            t = threading.Thread(
                target=listener._nag_loop,
                args=("T-test-005", state_file),
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        assert not state_file.exists()


class TestResumeOnRestart:
    def test_resume_starts_nag_for_pending_ticket(self, tmp_path):
        """On restart, nag resumes for tickets whose state files still exist."""
        import devices.granny.cc_worker_listener as mod
        nag_dir = tmp_path / "nag_state"
        nag_dir.mkdir()
        mod._NAG_STATE_DIR = nag_dir
        mod._NAG_INTERVAL_S = 1

        state_file = nag_dir / "T-resume-001.nag"
        state_file.write_text(json.dumps({"ticket_id": "T-resume-001", "session": "test.cc.0"}))

        started = threading.Event()
        original_nag_loop = CCWorkerListener._nag_loop

        def fake_nag_loop(self, ticket_id, sf):
            started.set()
            original_nag_loop(self, ticket_id, sf)

        def fake_run(args, **kw):
            if "show" in list(args):
                return MagicMock(returncode=0, stdout=json.dumps({"status": "sprint"}))
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None, nag_state_dir=nag_dir, nag_interval=9999)

        with patch.object(CCWorkerListener, "_nag_loop", fake_nag_loop), \
             patch("subprocess.run", side_effect=fake_run):
            listener._resume_pending_nags()
            assert started.wait(timeout=3), "nag thread was not started on resume"
            listener._stop.set()

    def test_resume_clears_stale_file_for_terminal_ticket(self, tmp_path):
        """On restart, state files for closed tickets are cleaned up."""
        import devices.granny.cc_worker_listener as mod
        nag_dir = tmp_path / "nag_state"
        nag_dir.mkdir()
        mod._NAG_STATE_DIR = nag_dir

        state_file = nag_dir / "T-stale-001.nag"
        state_file.write_text(json.dumps({"ticket_id": "T-stale-001", "session": "test.cc.0"}))

        def fake_run(args, **kw):
            if "show" in list(args):
                return MagicMock(returncode=0, stdout=json.dumps({"status": "closed"}))
            return MagicMock(returncode=0, stdout="{}")

        listener = _make_listener(imap=None, nag_state_dir=nag_dir)

        with patch("subprocess.run", side_effect=fake_run):
            listener._resume_pending_nags()

        assert not state_file.exists()
