"""
VetinariShim — lifecycle shim + directive intake listener.

Starts VetinariDevice and runs a DirectiveListener in a background thread.
The listener polls comms://vetinari.inbox for incoming bus envelopes, parses
each as a directive, and calls device.accept_directive() to persist it.

Design:
- BaseShim lifecycle (start / stop / restart / self_test / rollback)
- DirectiveListener: canonical fetch_unseen polling loop (no raw IMAP IDLE)
- Malformed envelopes: logged and skipped — never crash the listener
- Restart-safe: all state in flat-file pending_directives.json

T-vetinari-directive-intake.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_MAILBOX = os.environ.get("VETINARI_MAILBOX", "vetinari.inbox")
_POLL_INTERVAL_S = int(os.environ.get("VETINARI_POLL_INTERVAL", "5"))
_PROGRESS_POLL_INTERVAL_S = int(os.environ.get("VETINARI_PROGRESS_POLL_INTERVAL", "300"))  # 5 min


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_directive(env) -> dict:
    """Extract a normalised directive dict from a bus Envelope.

    Accepts both Envelope objects and raw JSON bytes/str. Returns a dict
    with keys: id, text, from, received_at. Raises ValueError on failure.
    """
    try:
        # Handle raw bytes/str from fetch_unseen
        if isinstance(env, (bytes, str)):
            data = json.loads(env if isinstance(env, str) else env.decode())
            payload = data.get("payload", {})
            from_device = data.get("from_device", "unknown")
            sent_at = data.get("sent_at", _now())
        else:
            payload = env.payload or {}
            from_device = env.from_device
            sent_at = env.sent_at
    except Exception as exc:
        raise ValueError(f"cannot parse envelope: {exc}") from exc

    text = (
        payload.get("text")
        or payload.get("directive")
        or (json.dumps(payload) if payload else "")
    )
    if not text:
        raise ValueError("envelope payload has no text or directive field")

    directive_id = payload.get("id") or sent_at
    return {
        "id": directive_id,
        "text": text,
        "from": from_device,
        "received_at": _now(),
    }


class DirectiveListener:
    """Background thread: poll vetinari.inbox, persist accepted directives.

    Canonical pattern: fetch_unseen → process each → sleep.
    Malformed envelopes are logged and skipped; listener never crashes.
    """

    def __init__(
        self,
        device,
        imap,
        mailbox: str = _MAILBOX,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._device = device
        self._imap = imap
        self._mailbox = mailbox
        self._stop = stop_event or threading.Event()

    def run_forever(self) -> None:
        log.info("DirectiveListener: started — mailbox=%s poll=%ds", self._mailbox, _POLL_INTERVAL_S)
        _last_progress_poll = 0.0
        while not self._stop.is_set():
            try:
                envelopes = self._imap.fetch_unseen(self._mailbox)
                for raw in envelopes:
                    self._process(raw)
            except Exception as exc:
                log.warning("DirectiveListener: fetch_unseen error: %s", exc)

            # Periodic progress poll for active directives (~5 min)
            now = time.time()
            if now - _last_progress_poll >= _PROGRESS_POLL_INTERVAL_S:
                self._poll_active_directives()
                self._sweep_system_alarms()
                _last_progress_poll = now

            self._stop.wait(timeout=_POLL_INTERVAL_S)
        log.info("DirectiveListener: stopped")

    def _poll_active_directives(self) -> None:
        """Check progress for all directives in 'active' status. Fail-open."""
        try:
            for directive in self._device.get_pending_directives():
                if directive.get("status") == "active" and directive.get("child_ticket_ids"):
                    self._device.check_directive_progress(directive["id"])
        except Exception as exc:
            log.warning("DirectiveListener: progress poll failed: %s", exc)

    def _sweep_system_alarms(self) -> None:
        """Sweep and escalate new/reopened system alarms. Fail-open."""
        try:
            count = self._device.sweep_system_alarms()
            if count > 0:
                log.info("DirectiveListener: swept %d system alarms", count)
        except Exception as exc:
            log.warning("DirectiveListener: alarm sweep failed: %s", exc)

    def _process(self, raw) -> None:
        try:
            directive = _parse_directive(raw)
        except ValueError as exc:
            log.warning("DirectiveListener: malformed envelope skipped: %s", exc)
            return
        try:
            added = self._device.accept_directive(directive)
            if added:
                log.info("DirectiveListener: directive %r accepted", directive.get("id"))
        except Exception as exc:
            log.warning("DirectiveListener: accept_directive failed: %s", exc)


class VetinariShim(BaseShim):
    """Lifecycle shim for VetinariDevice + DirectiveListener thread."""

    _device_id = "vetinari"

    def __init__(self, imap=None) -> None:
        from unseen_university.devices.vetinari.device import VetinariDevice

        self._device = VetinariDevice()
        self._imap = imap  # None → connect on start(); injected in tests
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: DirectiveListener | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        if self._imap is None:
            try:
                from unseen_university.devices.bus.connection import make_bus_connection
                self._imap = make_bus_connection()
            except Exception as exc:
                log.warning("VetinariShim: bus connection failed: %s", exc)
                return False

        self._stop.clear()
        self._listener = DirectiveListener(
            device=self._device,
            imap=self._imap,
            mailbox=_MAILBOX,
            stop_event=self._stop,
        )
        self._thread = threading.Thread(
            target=self._listener.run_forever,
            daemon=True,
            name="vetinari-listener",
        )
        self._thread.start()
        log.info("VetinariShim: started (listener thread alive)")
        return True

    def stop(self) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("VetinariShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        thread_alive = self._thread is not None and self._thread.is_alive()
        pending = len(self._device.get_pending_directives())
        return {
            "passed": thread_alive,
            "details": f"listener_alive={thread_alive} pending_directives={pending}",
        }

    def rollback(self) -> None:
        self.stop()
