"""
DickSimnelWorkerListener — bus dispatch listener for DickSimnel.0.

Polls the dicksimnel.0 mailbox for dispatch envelopes from Granny.
When a dispatch arrives:
  1. Sends dispatch_ack to Granny immediately.
  2. Checks OR balance floor (fail-open) and HIGH-inertia tags.
  3. Sends dispatch_started, then runs inference synchronously.
  4. Posts result (close) or escalates to CC.

Mirrors CCWorkerListener's bus pattern (devices/granny/cc_worker_listener.py)
but with synchronous delivery — the listener thread is occupied during work,
enforcing one-at-a-time execution without a prod loop.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = int(os.environ.get("DICKSIMNEL_LISTENER_POLL_INTERVAL", "5"))
_OR_BALANCE_FLOOR = float(os.environ.get("DICKSIMNEL_OR_FLOOR", "5.0"))
_FAILURE_THRESHOLD = 5  # consecutive receive failures before requesting reconnect

try:
    from devices.inference.budget_gate import fetch_balance
except ImportError:
    fetch_balance = None  # type: ignore[assignment]


class DickSimnelWorkerListener:
    """Polls dicksimnel.0 mailbox and works dispatched tickets synchronously."""

    def __init__(
        self,
        bus=None,
        device_mailbox: str = "dicksimnel.0",
        granny_mailbox: str = "granny.0",
        device=None,
        poll_interval: float = _POLL_INTERVAL_S,
        on_bus_failure=None,
    ) -> None:
        self._bus = bus
        self._device_mailbox = device_mailbox
        self._granny_mailbox = granny_mailbox
        self._device = device
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_failures = 0
        # Callable[[DickSimnelWorkerListener], None] — called after _FAILURE_THRESHOLD
        # consecutive receive failures. Shim injects this to handle reconnect.
        self.on_bus_failure = on_bus_failure

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="dicksimnel-listener")
        self._thread.start()
        log.info(
            "DickSimnelWorkerListener: started (mailbox=%s poll=%ss)",
            self._device_mailbox, self._poll_interval,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("DickSimnelWorkerListener: stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.warning("DickSimnelWorkerListener: poll error: %s", exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        if self._bus is None:
            return
        try:
            envelopes = self._bus.fetch_unseen(self._device_mailbox)
        except Exception as exc:
            self._consecutive_failures += 1
            log.warning(
                "DickSimnelWorkerListener: receive failed (%d/%d): %s",
                self._consecutive_failures, _FAILURE_THRESHOLD, exc,
            )
            if self._consecutive_failures >= _FAILURE_THRESHOLD and self.on_bus_failure:
                self._consecutive_failures = 0
                self.on_bus_failure(self)
            return
        self._consecutive_failures = 0

        for env in envelopes:
            payload = env.payload if hasattr(env, "payload") else {}
            kind = payload.get("kind")
            if kind == "dispatch":
                ticket_id = payload.get("ticket_id", "")
                log.info(
                    "DickSimnelWorkerListener: dispatch received ticket=%s from=%s",
                    ticket_id, env.from_device,
                )
                self._handle_dispatch(ticket_id, env.from_device)
            elif kind in ("halt", "priority"):
                log.info("DickSimnelWorkerListener: %s envelope — stopping listener", kind)
                self._stop.set()
                return

    def _send(self, to_device: str, payload: dict) -> None:
        if self._bus is None:
            return
        from bus.envelope import Envelope
        reply = Envelope.now(
            from_device=self._device_mailbox,
            to_device=to_device,
            payload=payload,
        )
        try:
            self._bus.append(self._granny_mailbox, reply)
        except Exception as exc:
            log.warning("DickSimnelWorkerListener: send failed to %s: %s", to_device, exc)

    def _handle_dispatch(self, ticket_id: str, reply_to: str) -> None:
        """Work one dispatched ticket synchronously. Blocks until done."""
        if not ticket_id:
            log.warning("DickSimnelWorkerListener: dispatch envelope missing ticket_id — ignoring")
            return

        # Ack immediately — Granny transitions ticket to 'acked'
        self._send(reply_to, {
            "kind": "dispatch_ack",
            "ticket_id": ticket_id,
            "from_device": self._device_mailbox,
        })
        log.info("DickSimnelWorkerListener: dispatch_ack sent ticket=%s", ticket_id)

        # OR balance floor: fail-open
        try:
            if fetch_balance is not None:
                bal = fetch_balance()
                if bal is not None and bal["balance"] <= _OR_BALANCE_FLOOR:
                    log.warning(
                        "DickSimnelWorkerListener: OR balance $%.2f at/below floor $%.2f"
                        " — declining %s",
                        bal["balance"], _OR_BALANCE_FLOOR, ticket_id,
                    )
                    if self._device is not None:
                        self._device._channel_event(
                            f"DICKSIMNEL_DECLINE ticket={ticket_id} reason='OR balance at floor'"
                        )
                        self._device._run_queue_cmd("setstatus", ticket_id, "sprint")
                    return
        except Exception as exc:
            log.debug("DickSimnelWorkerListener: budget check unavailable: %s", exc)

        if self._device is None:
            log.warning(
                "DickSimnelWorkerListener: no device wired — cannot work ticket %s", ticket_id
            )
            return

        # Fetch full ticket
        ticket = self._device._fetch_ticket(ticket_id)
        if ticket is None:
            log.warning(
                "DickSimnelWorkerListener: could not fetch ticket %s — escalating", ticket_id
            )
            self._device._escalate_ticket(ticket_id, "could not fetch ticket for dispatch")
            return

        # Pre-inference: bail on HIGH-inertia tags (saves cost; CC handles these)
        should_esc, esc_reason = self._device._should_escalate(ticket, None)
        if should_esc:
            self._device._escalate_ticket(ticket_id, esc_reason)
            return

        # Signal pickup — Granny transitions ticket to 'in_progress'
        self._send(reply_to, {
            "kind": "dispatch_started",
            "ticket_id": ticket_id,
            "from_device": self._device_mailbox,
        })
        self._device._active_ticket = ticket_id
        self._device._channel_event(
            f"DICKSIMNEL_WORKING ticket={ticket_id} title={ticket.get('title', '?')!r}"
        )
        log.info(
            "DickSimnelWorkerListener: working ticket %s — %s",
            ticket_id, ticket.get("title", "?"),
        )

        result = self._device._run_inference(ticket)

        if result is None:
            self._device._decline_ticket(ticket_id, "inference proxy unavailable or returned empty")
        else:
            should_esc, esc_reason = self._device._should_escalate(ticket, result)
            if should_esc:
                self._device._escalate_ticket(ticket_id, esc_reason, analysis=result)
            else:
                self._device._post_result(ticket_id, result)

        self._device._active_ticket = None
