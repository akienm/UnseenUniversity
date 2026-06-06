"""
cc_worker_listener.py — CC worker-side bus dispatch listener.

Listens on the cc.0 IMAP mailbox for dispatch envelopes from Granny.
When a dispatch arrives, calls BaseShim.receive_dispatch() to start the
two-phase handshake (ack → prod-every-120s → started/timeout).

The deliver_fn injects /sprint-ticket <ticket_id> into the CC tmux session.
The send_fn appends reply envelopes back to Granny's mailbox so the handshake
replies flow via the bus (not tmux).

Runs as a standalone process alongside the CC tmux session:
    python -m devices.granny.cc_worker_listener

Or directly:
    python devices/granny/cc_worker_listener.py
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_GRANNY_MAILBOX_DEFAULT = os.environ.get("GRANNY_MAILBOX", "granny.0")
_CC_MAILBOX_DEFAULT = os.environ.get("CC_MAILBOX", "cc.0")
_CC_SESSION_DEFAULT = (
    os.environ.get("CC_TMUX_SESSION")
    or socket.gethostname().split(".")[0].lower() + ".cc.0"
)
_POLL_INTERVAL_S = int(os.environ.get("CC_LISTENER_POLL_INTERVAL", "5"))
_PID_FILE = Path.home() / ".granny" / "cc_worker_listener.pid"


class CCWorkerListener:
    """Polls cc.0 bus mailbox for dispatch envelopes and drives the handshake."""

    def __init__(
        self,
        imap=None,
        cc_mailbox: str = _CC_MAILBOX_DEFAULT,
        granny_mailbox: str = _GRANNY_MAILBOX_DEFAULT,
        tmux_session: str = _CC_SESSION_DEFAULT,
        poll_interval: float = _POLL_INTERVAL_S,
    ) -> None:
        self._imap = imap
        self._cc_mailbox = cc_mailbox
        self._granny_mailbox = granny_mailbox
        self._tmux_session = tmux_session
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shim = _CCShimAdapter(device_id=cc_mailbox)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="cc-listener")
        self._thread.start()
        log.info("CCWorkerListener: started (mailbox=%s poll=%ss)", self._cc_mailbox, self._poll_interval)

    def stop(self) -> None:
        self._stop.set()
        self._shim.cancel_all()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("CCWorkerListener: stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.warning("CCWorkerListener: poll error: %s", exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        if self._imap is None:
            return
        try:
            envelopes = self._imap.fetch_unseen(self._cc_mailbox)
        except Exception as exc:
            log.warning("CCWorkerListener: fetch_unseen failed: %s", exc)
            return

        for env in envelopes:
            kind = env.payload.get("kind") if hasattr(env, "payload") else None
            if kind != "dispatch":
                continue
            log.info(
                "CCWorkerListener: dispatch envelope received ticket=%s from=%s",
                env.payload.get("ticket_id"),
                env.from_device,
            )
            self._shim.receive_dispatch(
                env,
                send_fn=self._make_send_fn(),
                deliver_fn=self._make_deliver_fn(),
            )

    def _make_send_fn(self):
        imap = self._imap
        granny_mailbox = self._granny_mailbox
        cc_mailbox = self._cc_mailbox

        def send_fn(to_device: str, payload: dict) -> None:
            from bus.envelope import Envelope
            reply = Envelope.now(
                from_device=cc_mailbox,
                to_device=to_device,
                payload=payload,
            )
            try:
                imap.append(granny_mailbox, reply)
            except Exception as exc:
                log.warning("CCWorkerListener: send_fn append failed: %s", exc)

        return send_fn

    def _make_deliver_fn(self):
        session = self._tmux_session

        def deliver_fn(ticket_id: str) -> bool:
            check = subprocess.run(
                ["tmux", "has-session", "-t", session],
                capture_output=True,
            )
            if check.returncode != 0:
                log.warning("CCWorkerListener: tmux session %r not found", session)
                return False
            subprocess.run(
                ["tmux", "send-keys", "-t", session, f"\r\r\r/sprint-ticket {ticket_id}\r"],
                check=False,
            )
            log.info("CCWorkerListener: injected /sprint-ticket %s into %s", ticket_id, session)
            return True

        return deliver_fn


class _CCShimAdapter:
    """Minimal shim adapter so CCWorkerListener can call receive_dispatch()."""

    def __init__(self, device_id: str) -> None:
        self._device_id = device_id
        self._active_handshakes: dict = {}

    @property
    def device_id(self) -> str:
        return self._device_id

    def receive_dispatch(self, envelope, *, send_fn, deliver_fn=None, **kwargs):
        from unseen_university.shim import BaseShim
        return BaseShim.receive_dispatch(self, envelope, send_fn=send_fn, deliver_fn=deliver_fn, **kwargs)

    def cancel_all(self) -> None:
        for hs in list(getattr(self, "_active_handshakes", {}).values()):
            try:
                hs.stop()
            except Exception:
                pass
        self._active_handshakes.clear()


def _make_imap():
    from bus.connection import make_bus_connection
    return make_bus_connection()


def run_forever(
    cc_mailbox: str = _CC_MAILBOX_DEFAULT,
    granny_mailbox: str = _GRANNY_MAILBOX_DEFAULT,
    tmux_session: str = _CC_SESSION_DEFAULT,
) -> None:
    imap = _make_imap()
    listener = CCWorkerListener(
        imap=imap,
        cc_mailbox=cc_mailbox,
        granny_mailbox=granny_mailbox,
        tmux_session=tmux_session,
    )

    def _handle_sig(sig, _frame):
        log.info("CCWorkerListener: signal %s — stopping", sig)
        listener.stop()
        _PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))

    listener.start()
    log.info("CCWorkerListener: running (pid=%d, mailbox=%s)", os.getpid(), cc_mailbox)

    try:
        while True:
            time.sleep(60)
    finally:
        listener.stop()
        _PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever()
