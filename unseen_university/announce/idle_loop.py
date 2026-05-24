"""
AnnounceIdleLoop — background thread that wakes on IMAP IDLE EXISTS and runs
a configured callable.

Production wakes the announce broker within milliseconds of an envelope
landing on comms://announce instead of polling. The loop wraps any callable
(typically Skeleton.announce_pump) so the thread itself doesn't know about
the broker — keeps responsibilities clean.

Slice 3b scope: single-shot loop with clean stop. Reconnect-on-disconnect
is a slice-3b-2 follow-up. Loop holds its own imaplib client (not shared
with IMAPServer's internal client) so socket-close shutdown doesn't tear
down other consumers.
"""

from __future__ import annotations

import imaplib
import logging
import socket as _socket
import threading

log = logging.getLogger(__name__)


class AnnounceIdleLoop:
    """
    Watch a single mailbox over IMAP IDLE; call the callback on each EXISTS.

    Args:
        host:     IMAP host, e.g. "127.0.0.1".
        port:     IMAP port (10143 in test mode, 143 in production).
        mailbox:  mailbox to watch (e.g. ANNOUNCE_MAILBOX).
        callback: zero-arg callable invoked once per EXISTS notification.
                  Exceptions raised by callback are logged and the loop
                  continues to wait for the next EXISTS.
        user:     IMAP username (stub accepts anything; production needs
                  real credentials).
        password: IMAP password.
    """

    def __init__(
        self,
        host: str,
        port: int,
        mailbox: str,
        callback,
        user: str = "user",
        password: str = "pass",
    ) -> None:
        self._host = host
        self._port = port
        self._mailbox = mailbox
        self._callback = callback
        self._user = user
        self._password = password
        self._client: imaplib.IMAP4 | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the daemon loop. Idempotent — second call no-ops."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """
        Signal shutdown and force the IDLE readline() to return by shutting
        the socket from the outer thread. Joins the loop thread with a
        bounded timeout.

        Calling sock.shutdown(SHUT_RDWR) directly (not imaplib.shutdown())
        because the latter closes self.file first, which can deadlock when
        the read thread is blocked inside that same file's readline.
        """
        self._stop.set()
        client = self._client
        if client is not None:
            try:
                client.sock.shutdown(_socket.SHUT_RDWR)
            except OSError:
                # Socket already closed — fine.
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            self._client = imaplib.IMAP4(self._host, self._port)
            self._client.login(self._user, self._password)
            self._client.select(self._mailbox)
        except Exception as exc:
            log.warning(
                "idle-loop: connect/login/select failed for %s: %s",
                self._mailbox,
                exc,
            )
            return

        # Note: callback fires once at startup so the loop catches up on any
        # envelopes that arrived before IDLE was registered.
        self._safe_callback("startup-catchup")

        while not self._stop.is_set():
            try:
                if not self._idle_once():
                    break  # socket closed by stop()
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("idle-loop: unexpected error in IDLE cycle: %s", exc)
                # Brief sleep so a hot loop doesn't burn CPU on persistent errors.
                self._stop.wait(timeout=0.5)

        # Cleanup — best effort; the socket may already be torn down by stop().
        # Skip imaplib.logout() (would try to send LOGOUT on a dead socket and
        # may deadlock); just close the socket if it's still open.
        if self._client is not None:
            try:
                self._client.sock.close()
            except Exception:
                pass
        self._client = None

    def _idle_once(self) -> bool:
        """
        Send IDLE, wait for EXISTS, send DONE, run callback. Returns False
        when the connection has been closed (stop() was called); True
        otherwise.
        """
        client = self._client
        if client is None:
            return False
        try:
            client.send(b"A001 IDLE\r\n")
            confirm = client.readline()  # "+ idling"
            if not confirm:
                return False
            # Block until EXISTS arrives (or socket closes).
            line = client.readline()
            if not line:
                return False
            saw_exists = b"EXISTS" in line
            try:
                client.send(b"A002 DONE\r\n")
                client.readline()  # OK IDLE terminated
            except Exception:
                # Socket might have closed between read and send — fine.
                return False
        except (OSError, imaplib.IMAP4.abort) as exc:
            if self._stop.is_set():
                return False
            log.warning("idle-loop: IDLE I/O error: %s", exc)
            return False

        if saw_exists:
            self._safe_callback("exists")
        return True

    def _safe_callback(self, source: str) -> None:
        try:
            self._callback()
        except Exception as exc:
            log.warning("idle-loop: callback (%s) raised — continuing: %s", source, exc)
