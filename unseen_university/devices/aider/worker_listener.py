"""
AiderWorkerListener — bus dispatch listener for an Aider.N builder.

Polls the aider.N mailbox for Granny dispatch envelopes. On a dispatch:
  1. dispatch_ack to Granny immediately.
  2. Decline HIGH-inertia tags (CC handles those).
  3. dispatch_started, then runner build synchronously (blocking — one at a time).
  4. gate PASS -> close; gate FAIL -> escalate to CC. dispatch_done to Granny.

Mirrors DickSimnelWorkerListener's synchronous bus pattern, minus the OR-balance
floor: aider runs on Hex ollama at $0, so there is no cost gate to check.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = int(os.environ.get("AIDER_LISTENER_POLL_INTERVAL", "5"))
_FAILURE_THRESHOLD = 5


class AiderWorkerListener:
    """Polls the aider.N mailbox and builds dispatched tickets synchronously."""

    def __init__(
        self,
        bus=None,
        device_mailbox: str = "aider.0",
        granny_mailbox: str = "granny.0",
        device=None,
        poll_interval: float = _POLL_INTERVAL_S,
        on_bus_failure=None,
        on_idle_shutdown=None,
    ) -> None:
        self._bus = bus
        self._device_mailbox = device_mailbox
        self._granny_mailbox = granny_mailbox
        self._device = device
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_failures = 0
        self.on_bus_failure = on_bus_failure
        self._on_idle_shutdown = on_idle_shutdown
        self._idle_timeout_s = float(os.environ.get("AIDER_IDLE_TIMEOUT_S", "120"))
        self._last_active = None

    def start(self) -> None:
        self._last_active = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="aider-listener")
        self._thread.start()
        log.info("AiderWorkerListener: started (mailbox=%s poll=%ss)",
                 self._device_mailbox, self._poll_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("AiderWorkerListener: stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.warning("AiderWorkerListener: poll error: %s", exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        if self._bus is None:
            return
        try:
            envelopes = self._bus.fetch_unseen(self._device_mailbox)
        except Exception as exc:
            self._consecutive_failures += 1
            log.warning("AiderWorkerListener: receive failed (%d/%d): %s",
                        self._consecutive_failures, _FAILURE_THRESHOLD, exc)
            if self._consecutive_failures >= _FAILURE_THRESHOLD and self.on_bus_failure:
                self._consecutive_failures = 0
                self.on_bus_failure(self)
            return
        self._consecutive_failures = 0

        dispatch_seen = False
        quit_timeout = None
        for env in envelopes:
            payload = env.payload if hasattr(env, "payload") else {}
            kind = payload.get("kind")
            if kind == "dispatch":
                ticket_id = payload.get("ticket_id", "")
                log.info("AiderWorkerListener: dispatch received ticket=%s from=%s",
                         ticket_id, env.from_device)
                dispatch_seen = True
                self._handle_dispatch(ticket_id, env.from_device)
            elif kind in ("halt", "priority"):
                log.info("AiderWorkerListener: %s envelope — stopping listener", kind)
                self._stop.set()
                return
            elif kind == "quit_if_idle":
                quit_timeout = payload.get("idle_timeout", self._idle_timeout_s)

        if (quit_timeout is not None and not dispatch_seen and self._device is not None
                and self._device._active_ticket is None and self._last_active is not None):
            idle_elapsed = time.monotonic() - self._last_active
            if idle_elapsed >= quit_timeout:
                log.info("idle-sleep: %s idle %.0fs — self-exiting", self._device_mailbox, idle_elapsed)
                if self._on_idle_shutdown:
                    self._on_idle_shutdown()

    def _send(self, to_device: str, payload: dict) -> None:
        if self._bus is None:
            return
        from unseen_university.devices.bus.envelope import Envelope
        reply = Envelope.now(from_device=self._device_mailbox, to_device=to_device, payload=payload)
        try:
            self._bus.append(self._granny_mailbox, reply)
        except Exception as exc:
            log.warning("AiderWorkerListener: send failed to %s: %s", to_device, exc)

    def _handle_dispatch(self, ticket_id: str, reply_to: str) -> None:
        """Build one dispatched ticket synchronously. Blocks until done."""
        if not ticket_id:
            log.warning("AiderWorkerListener: dispatch envelope missing ticket_id — ignoring")
            return

        self._send(reply_to, {"kind": "dispatch_ack", "ticket_id": ticket_id,
                              "from_device": self._device_mailbox})
        log.info("AiderWorkerListener: dispatch_ack sent ticket=%s", ticket_id)

        if self._device is None:
            log.warning("AiderWorkerListener: no device wired — cannot build ticket %s", ticket_id)
            return

        ticket = self._device._fetch_ticket(ticket_id)
        if ticket is None:
            log.warning("AiderWorkerListener: could not fetch ticket %s — escalating", ticket_id)
            self._send_done(reply_to, ticket_id,
                            self._device._escalation_artifact(ticket_id, "could not fetch ticket for dispatch"))
            return

        should_esc, esc_reason = self._device._should_escalate(ticket)
        if should_esc:
            self._send_done(reply_to, ticket_id,
                            self._device._escalation_artifact(ticket_id, esc_reason))
            return

        self._send(reply_to, {"kind": "dispatch_started", "ticket_id": ticket_id,
                              "from_device": self._device_mailbox})
        self._device._active_ticket = ticket_id
        self._device._channel_event(
            f"AIDER_WORKING ticket={ticket_id} title={ticket.get('title', '?')!r}", event_type="working")
        log.info("AiderWorkerListener: building ticket %s — %s", ticket_id, ticket.get("title", "?"))

        try:
            result = self._device._run_build(ticket)
        except Exception as exc:
            log.warning("AiderWorkerListener: build raised for %s: %s", ticket_id, exc)
            artifact = self._device._escalation_artifact(ticket_id, f"build raised: {exc}")
        else:
            artifact = self._device._build_report(ticket_id, result)

        self._send_done(reply_to, ticket_id, artifact)
        self._device._active_ticket = None
        self._last_active = time.monotonic()

    def _send_done(self, reply_to: str, ticket_id: str, artifact: dict) -> None:
        """Send dispatch_done CARRYING the builder's result artifact for Granny to
        reconcile (single-writer, D-granny-sole-ticket-writer). The artifact fields
        (outcome/branch/changed_files/note/missing_lever/reason/analysis) ride the
        envelope — the builder writes no ticket state itself."""
        self._send(reply_to, {"kind": "dispatch_done", "ticket_id": ticket_id,
                              "from_device": self._device_mailbox, **artifact})
        log.info("AiderWorkerListener: task_outcome|ticket=%s|outcome=%s",
                 ticket_id, artifact.get("outcome"))
