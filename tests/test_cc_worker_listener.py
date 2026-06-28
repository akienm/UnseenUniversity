"""Tests for CCWorkerListener — CC worker-side bus dispatch listener.

Completion criteria (T-cc-worker-idle-listener):
  - dispatch envelope arrives → ack sent to granny mailbox synchronously
  - deliver_fn returns True → started sent
  - non-dispatch envelopes ignored
  - tmux session not found → deliver_fn returns False (no inject)
  - imap fetch failure → poll_once returns silently
"""

from __future__ import annotations

import collections
import threading
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.granny.cc_worker_listener import CCWorkerListener, _CCShimAdapter


# ── Fake IMAP ─────────────────────────────────────────────────────────────────


class _FakeIMAP:
    def __init__(self):
        self._boxes: dict[str, list] = collections.defaultdict(list)
        self._read: dict[str, int] = collections.defaultdict(int)

    def append(self, mailbox: str, envelope) -> None:
        self._boxes[mailbox].append(envelope)

    def fetch_unseen(self, mailbox: str) -> list:
        seen = self._read[mailbox]
        new = self._boxes[mailbox][seen:]
        self._read[mailbox] = len(self._boxes[mailbox])
        return new

    def inject(self, mailbox: str, kind: str, ticket_id: str) -> None:
        env = Envelope.now(
            from_device="granny.0",
            to_device=mailbox,
            payload={"kind": kind, "ticket_id": ticket_id},
        )
        self.append(mailbox, env)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCCWorkerListenerPollOnce:
    def _listener(self, imap, tmux_ok=True, tmux_session="claude-main"):
        listener = CCWorkerListener(
            imap=imap,
            cc_mailbox="cc.0",
            granny_mailbox="granny.0",
            tmux_session=tmux_session,
            poll_interval=999,
        )
        tmux_rc = 0 if tmux_ok else 1
        _tmux_check = MagicMock(return_value=MagicMock(returncode=tmux_rc))
        _tmux_inject = MagicMock(return_value=None)

        def _mock_run(cmd, **kwargs):
            if "has-session" in cmd:
                return _tmux_check(cmd, **kwargs)
            return _tmux_inject(cmd, **kwargs)

        return listener, _mock_run, _tmux_check, _tmux_inject

    def test_dispatch_envelope_sends_ack_to_granny(self):
        imap = _FakeIMAP()
        imap.inject("cc.0", "dispatch", "T-foo")
        listener, mock_run, _, _ = self._listener(imap)

        with patch("subprocess.run", side_effect=mock_run):
            listener._poll_once()

        acks = [e for e in imap._boxes.get("granny.0", [])
                if e.payload.get("kind") == "dispatch_ack"]
        assert len(acks) == 1
        assert acks[0].payload["ticket_id"] == "T-foo"
        assert acks[0].from_device == "cc.0"

    def test_deliver_fn_notifies_when_tmux_found(self):
        """deliver_fn directly: tmux session present → send-keys soft notification."""
        imap = _FakeIMAP()
        listener, mock_run, _, tmux_inject = self._listener(imap)
        deliver_fn = listener._make_deliver_fn()

        with patch("subprocess.run", side_effect=mock_run):
            result = deliver_fn("T-bar")

        assert result is True
        injected_cmds = [str(c) for c in tmux_inject.call_args_list]
        # Must notify "ticket waiting", must NOT inject /sprint-ticket directly
        assert any("T-bar" in c and "ticket waiting" in c for c in injected_cmds)
        assert not any("sprint-ticket" in c for c in injected_cmds)

    def test_non_dispatch_envelope_ignored(self):
        imap = _FakeIMAP()
        imap.inject("cc.0", "heartbeat", "T-ignored")
        listener, mock_run, _, tmux_inject = self._listener(imap)

        with patch("subprocess.run", side_effect=mock_run):
            listener._poll_once()

        # No ack should have been sent
        assert not imap._boxes.get("granny.0")

    def test_tmux_session_not_found_returns_false(self):
        """deliver_fn directly: tmux session absent → returns False, no inject."""
        imap = _FakeIMAP()
        listener, mock_run, _, tmux_inject = self._listener(imap, tmux_ok=False)
        deliver_fn = listener._make_deliver_fn()

        with patch("subprocess.run", side_effect=mock_run):
            result = deliver_fn("T-notmux")

        assert result is False
        inject_calls = [str(c) for c in tmux_inject.call_args_list]
        assert not any("send-keys" in c for c in inject_calls)

    def test_fetch_failure_is_silent(self):
        class _BrokenIMAP:
            def fetch_unseen(self, *_):
                raise OSError("IMAP down")

        listener = CCWorkerListener(imap=_BrokenIMAP(), poll_interval=999)
        # Must not raise
        listener._poll_once()

    def test_empty_mailbox_no_action(self):
        imap = _FakeIMAP()
        listener, mock_run, _, tmux_inject = self._listener(imap)

        with patch("subprocess.run", side_effect=mock_run):
            listener._poll_once()

        assert not imap._boxes.get("granny.0")


class TestPriorityHaltInterrupt:
    """Priority/HALT envelopes fire the tmux interrupt sequence immediately."""

    def _listener(self, imap, tmux_ok=True):
        listener = CCWorkerListener(
            imap=imap,
            cc_mailbox="cc.0",
            granny_mailbox="granny.0",
            tmux_session="test-session",
            poll_interval=999,
        )
        tmux_rc = 0 if tmux_ok else 1

        def _mock_run(cmd, **kwargs):
            return MagicMock(returncode=tmux_rc)

        return listener, _mock_run

    def _inject(self, imap, kind, message="STOP now"):
        env = Envelope.now(
            from_device="granny.0",
            to_device="cc.0",
            payload={"kind": kind, "message": message},
        )
        imap.append("cc.0", env)

    def test_priority_envelope_fires_interrupt(self):
        imap = _FakeIMAP()
        self._inject(imap, "priority", "urgent: human override")
        listener, mock_run = self._listener(imap)
        sent = []

        def _capture_run(cmd, **kwargs):
            sent.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture_run):
            listener._poll_once()

        injected = [c for c in sent if "send-keys" in c]
        assert len(injected) == 1
        assert "urgent: human override" in injected[0][-1]
        assert injected[0][-1].startswith("\r\r\r")

    def test_halt_envelope_fires_interrupt(self):
        imap = _FakeIMAP()
        self._inject(imap, "halt", "HALT: system shutdown")
        listener, mock_run = self._listener(imap)
        sent = []

        def _capture_run(cmd, **kwargs):
            sent.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture_run):
            listener._poll_once()

        injected = [c for c in sent if "send-keys" in c]
        assert len(injected) == 1
        assert "HALT: system shutdown" in injected[0][-1]

    def test_interrupt_skipped_when_session_not_found(self):
        imap = _FakeIMAP()
        self._inject(imap, "halt", "stop please")
        listener, _ = self._listener(imap, tmux_ok=False)
        sent = []

        def _capture_run(cmd, **kwargs):
            sent.append(cmd)
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=_capture_run):
            listener._poll_once()

        # has-session ran; send-keys did NOT
        assert not any("send-keys" in c for c in sent)

    def test_interrupt_message_defaults_to_kind_and_sender(self):
        """Envelope with no message field gets a default message body."""
        imap = _FakeIMAP()
        env = Envelope.now(
            from_device="igor.0",
            to_device="cc.0",
            payload={"kind": "halt"},  # no 'message' key
        )
        imap.append("cc.0", env)
        listener, _ = self._listener(imap)
        sent = []

        def _capture_run(cmd, **kwargs):
            sent.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture_run):
            listener._poll_once()

        injected = [c for c in sent if "send-keys" in c]
        assert len(injected) == 1
        # default body contains kind and sender
        assert "halt" in injected[0][-1]
        assert "igor.0" in injected[0][-1]


class TestCCShimAdapter:
    def test_device_id_returns_mailbox(self):
        adapter = _CCShimAdapter(device_id="cc.0")
        assert adapter.device_id == "cc.0"

    def test_cancel_all_stops_active_handshakes(self):
        adapter = _CCShimAdapter(device_id="cc.0")
        mock_hs = MagicMock()
        adapter._active_handshakes["T-x"] = mock_hs
        adapter.cancel_all()
        mock_hs.stop.assert_called_once()
        assert not adapter._active_handshakes
