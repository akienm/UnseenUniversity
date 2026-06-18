"""
cc_worker_listener.py — CC worker-side bus dispatch listener.

Listens on the cc.0 IMAP mailbox for dispatch envelopes from Granny.
When a dispatch arrives:
  1. Calls BaseShim.receive_dispatch() → sends dispatch_ack to Granny immediately.
  2. Appends "CC.0 acked at <timestamp>" note to the ticket.
  3. Starts a per-ticket nag thread: every CC_SHIM_NAG_INTERVAL seconds (default
     600), if the ticket is still not in_progress, fires a soft tmux nudge
     (\r\r\rcheck messages when possible\n). Stops when ticket reaches in_progress
     or a terminal status.

Nag state is persisted to ~/.granny/nag_state/<ticket_id>.nag so a listener
restart can resume nagging for any still-pending tickets.

Runs as a standalone process alongside the CC tmux session:
    python -m devices.granny.cc_worker_listener

Or directly:
    python devices/granny/cc_worker_listener.py
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_GRANNY_MAILBOX_DEFAULT = os.environ.get("GRANNY_MAILBOX", "granny.0")
_CC_MAILBOX_DEFAULT = os.environ.get("CC_MAILBOX", "cc.0")
_CC_SESSION_DEFAULT = (
    os.environ.get("CC_TMUX_SESSION")
    or socket.gethostname().split(".")[0].lower() + ".cc.0"
)
_POLL_INTERVAL_S = int(os.environ.get("CC_LISTENER_POLL_INTERVAL", "5"))
_NAG_INTERVAL_S = int(os.environ.get("CC_SHIM_NAG_INTERVAL", "600"))
_NAG_MSG = "\r\r\rcheck messages when possible\n"
_NAG_STATE_DIR = Path.home() / ".granny" / "nag_state"
_NAG_TERMINAL_STATUSES = frozenset({"in_progress", "done", "closed", "cancelled", "discarded"})
# Per-slot pid file: CCWorkerShim passes CC_LISTENER_PID_FILE so CC.0 and CC.1 never collide.
_PID_FILE = Path(
    os.environ.get(
        "CC_LISTENER_PID_FILE",
        str(Path.home() / ".granny" / "cc_worker_listener.pid"),
    )
)


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
        self._nag_threads: dict[str, threading.Thread] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="cc-listener")
        self._thread.start()
        self._resume_pending_nags()
        log.info("CCWorkerListener: started (mailbox=%s poll=%ss nag=%ds)",
                 self._cc_mailbox, self._poll_interval, _NAG_INTERVAL_S)

    def stop(self) -> None:
        self._stop.set()
        self._shim.cancel_all()
        if self._thread:
            self._thread.join(timeout=5)
        for t in list(self._nag_threads.values()):
            t.join(timeout=2)
        log.info("CCWorkerListener: stopped")

    def is_alive(self) -> bool:
        """True when the listener poll thread is running."""
        return self._thread is not None and self._thread.is_alive()

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
            if kind == "dispatch":
                ticket_id = env.payload.get("ticket_id") if hasattr(env, "payload") else None
                log.info(
                    "CCWorkerListener: dispatch envelope received ticket=%s from=%s",
                    ticket_id,
                    env.from_device,
                )
                self._shim.receive_dispatch(
                    env,
                    send_fn=self._make_send_fn(),
                    deliver_fn=self._make_deliver_fn(),
                )
                if ticket_id:
                    self._add_ack_note(ticket_id)
                    self._start_nag_thread(ticket_id)
            elif kind in ("priority", "halt"):
                message = env.payload.get("message") or f"[{kind} from {env.from_device}]"
                log.info(
                    "CCWorkerListener: %s envelope received from=%s message=%r",
                    kind, env.from_device, message,
                )
                self._fire_interrupt(message)

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
                ["tmux", "send-keys", "-t", session, f"\r\r\ryou have a ticket waiting: {ticket_id}\r"],
                check=False,
            )
            log.info("CCWorkerListener: notified %s of waiting ticket=%s", session, ticket_id)
            return True

        return deliver_fn

    # ── Priority / HALT interrupt ────────────────────────────────────────────────

    def _fire_interrupt(self, message: str) -> None:
        """Inject 3x Enter then message body into the CC tmux session — synchronous."""
        session = self._tmux_session
        check = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
        )
        if check.returncode != 0:
            log.warning("CCWorkerListener: interrupt skipped — tmux session %r not found", session)
            return
        subprocess.run(
            ["tmux", "send-keys", "-t", session, f"\r\r\r{message}\r"],
            check=False,
        )
        log.info("CCWorkerListener: interrupt injected into %s: %r", session, message)

    # ── Ack note ────────────────────────────────────────────────────────────────

    def _cc_queue_path(self) -> str:
        tools = os.environ.get(
            "CC_WORKFLOW_TOOLS",
            str(Path.home() / "TheIgors" / "devlab" / "claudecode"),
        )
        return str(Path(tools) / "cc_queue.py")

    def _add_ack_note(self, ticket_id: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        note = f"CC.0 acked at {ts}"
        try:
            subprocess.run(
                [sys.executable, self._cc_queue_path(), "append-note", ticket_id, note],
                capture_output=True, check=False,
            )
            log.info("CCWorkerListener: ack note added ticket=%s ts=%s", ticket_id, ts)
        except Exception as exc:
            log.warning("CCWorkerListener: failed to add ack note for %s: %s", ticket_id, exc)

    # ── Nag loop ─────────────────────────────────────────────────────────────────

    def _get_ticket_status(self, ticket_id: str) -> str | None:
        """Query cc_queue for current ticket status. Returns None on error."""
        try:
            result = subprocess.run(
                [sys.executable, self._cc_queue_path(), "show", ticket_id],
                capture_output=True, text=True, check=False,
            )
            data = json.loads(result.stdout)
            return data.get("status")
        except Exception as exc:
            log.warning("CCWorkerListener: status check failed for %s: %s", ticket_id, exc)
            return None

    def _start_nag_thread(self, ticket_id: str) -> None:
        _NAG_STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file = _NAG_STATE_DIR / f"{ticket_id}.nag"
        state_file.write_text(json.dumps({"ticket_id": ticket_id, "session": self._tmux_session}))
        t = threading.Thread(
            target=self._nag_loop,
            args=(ticket_id, state_file),
            daemon=True,
            name=f"nag-{ticket_id}",
        )
        t.start()
        self._nag_threads[ticket_id] = t
        log.info("CCWorkerListener: nag thread started ticket=%s interval=%ds", ticket_id, _NAG_INTERVAL_S)

    def _nag_loop(self, ticket_id: str, state_file: Path) -> None:
        while not self._stop.is_set():
            if self._stop.wait(timeout=_NAG_INTERVAL_S):
                break
            status = self._get_ticket_status(ticket_id)
            if status in _NAG_TERMINAL_STATUSES or status is None:
                log.info("CCWorkerListener: nag stopping ticket=%s status=%s", ticket_id, status)
                break
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", self._tmux_session, _NAG_MSG],
                    check=False, capture_output=True,
                )
                log.info("CCWorkerListener: nag sent ticket=%s session=%s", ticket_id, self._tmux_session)
            except Exception as exc:
                log.warning("CCWorkerListener: nag send failed for %s: %s", ticket_id, exc)
        state_file.unlink(missing_ok=True)
        self._nag_threads.pop(ticket_id, None)

    def _resume_pending_nags(self) -> None:
        """On restart: resume nag threads for any tickets still in nag_state/."""
        if not _NAG_STATE_DIR.exists():
            return
        for state_file in _NAG_STATE_DIR.glob("*.nag"):
            try:
                data = json.loads(state_file.read_text())
                ticket_id = data["ticket_id"]
                status = self._get_ticket_status(ticket_id)
                if status in _NAG_TERMINAL_STATUSES or status is None:
                    state_file.unlink(missing_ok=True)
                    log.info("CCWorkerListener: stale nag cleared ticket=%s status=%s", ticket_id, status)
                    continue
                t = threading.Thread(
                    target=self._nag_loop,
                    args=(ticket_id, state_file),
                    daemon=True,
                    name=f"nag-{ticket_id}",
                )
                t.start()
                self._nag_threads[ticket_id] = t
                log.info("CCWorkerListener: resumed nag ticket=%s", ticket_id)
            except Exception as exc:
                log.warning("CCWorkerListener: could not resume nag from %s: %s", state_file, exc)


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


def _mailbox_to_worker_id(mailbox: str) -> str:
    """Derive Granny worker_id from mailbox name. cc.0 → CC.0, cc.1 → CC.1."""
    parts = mailbox.split(".")
    if len(parts) == 2:
        return f"{parts[0].upper()}.{parts[1]}"
    return mailbox.upper()


def run_forever(
    cc_mailbox: str = _CC_MAILBOX_DEFAULT,
    granny_mailbox: str = _GRANNY_MAILBOX_DEFAULT,
    tmux_session: str = _CC_SESSION_DEFAULT,
) -> None:
    from devices.granny.announce_worker import announce, withdraw

    imap = _make_imap()
    listener = CCWorkerListener(
        imap=imap,
        cc_mailbox=cc_mailbox,
        granny_mailbox=granny_mailbox,
        tmux_session=tmux_session,
    )

    worker_id = _mailbox_to_worker_id(cc_mailbox)
    is_cc0 = cc_mailbox == "cc.0"

    def _handle_sig(sig, _frame):
        log.info("CCWorkerListener: signal %s — stopping", sig)
        listener.stop()
        withdraw(worker_id)
        _PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))

    # Announce to Granny's dynamic dispatch registry.
    announce(
        worker_id,
        mailbox=cc_mailbox,
        worker_name="claude" if is_cc0 else cc_mailbox,  # "claude" matches legacy ticket worker field
        one_at_a_time=True,
        cascade_if_idle=is_cc0,  # only CC.0 cascade-absorbs builder tickets
    )

    listener.start()
    log.info(
        "CCWorkerListener: running (pid=%d, worker=%s, mailbox=%s)",
        os.getpid(), worker_id, cc_mailbox,
    )

    try:
        while True:
            time.sleep(60)
    finally:
        listener.stop()
        withdraw(worker_id)
        _PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever()
