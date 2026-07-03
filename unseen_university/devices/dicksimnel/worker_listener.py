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
import time

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = int(os.environ.get("DICKSIMNEL_LISTENER_POLL_INTERVAL", "5"))
_OR_BALANCE_FLOOR = float(os.environ.get("DICKSIMNEL_OR_FLOOR", "5.0"))
_FAILURE_THRESHOLD = 5  # consecutive receive failures before requesting reconnect

try:
    from unseen_university.devices.inference.budget_gate import fetch_balance
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
        # Callable[[DickSimnelWorkerListener], None] — called after _FAILURE_THRESHOLD
        # consecutive receive failures. Shim injects this to handle reconnect.
        self.on_bus_failure = on_bus_failure
        # Callable[[], None] — called when instance detects idle timeout. Shim injects
        # this to trigger clean shutdown (SIGTERM).
        self._on_idle_shutdown = on_idle_shutdown
        # Idle clock — tracks last active ticket completion
        self._idle_timeout_s = float(os.environ.get("DICKSIMNEL_IDLE_TIMEOUT_S", "120"))
        self._last_active = None

    def start(self) -> None:
        self._last_active = time.monotonic()
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

        # Drain-safe: track if we saw any dispatch or quit_if_idle in this batch
        dispatch_seen = False
        quit_timeout = None

        for env in envelopes:
            payload = env.payload if hasattr(env, "payload") else {}
            kind = payload.get("kind")
            if kind == "dispatch":
                ticket_id = payload.get("ticket_id", "")
                log.info(
                    "DickSimnelWorkerListener: dispatch received ticket=%s from=%s",
                    ticket_id, env.from_device,
                )
                dispatch_seen = True
                self._handle_dispatch(ticket_id, env.from_device)
            elif kind in ("halt", "priority"):
                log.info("DickSimnelWorkerListener: %s envelope — stopping listener", kind)
                self._stop.set()
                return
            elif kind == "quit_if_idle":
                # Fire-and-forget: do NOT act in loop — just record the timeout for after-loop check
                quit_timeout = payload.get("idle_timeout", self._idle_timeout_s)

        # After draining batch: check idle shutdown (only if no dispatch was in this batch)
        if (quit_timeout is not None and not dispatch_seen and self._device is not None
                and self._device._active_ticket is None and self._last_active is not None):
            idle_elapsed = time.monotonic() - self._last_active
            if idle_elapsed >= quit_timeout:
                log.info(
                    "idle-sleep: %s idle %.0fs — self-exiting",
                    self._device_mailbox, idle_elapsed
                )
                if self._on_idle_shutdown:
                    self._on_idle_shutdown()

    def _send(self, to_device: str, payload: dict) -> None:
        if self._bus is None:
            return
        from unseen_university.devices.bus.envelope import Envelope
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
                            f"DICKSIMNEL_DECLINE ticket={ticket_id} reason='OR balance at floor'",
                            event_type="decline",
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
            f"DICKSIMNEL_WORKING ticket={ticket_id} title={ticket.get('title', '?')!r}",
            event_type="working",
        )
        log.info(
            "DickSimnelWorkerListener: working ticket %s — %s",
            ticket_id, ticket.get("title", "?"),
        )

        result = self._device._run_inference(ticket)

        # Task-level outcome (did the routed inference actually finish the work) —
        # logged keyed by ticket_id so it joins the device-side per-call cost_record
        # for the same ticket (T-inference-cost-learn-verify). 'fail' | 'escalated' | 'done'.
        _task_outcome = "done" if result is not None else "fail"

        if result is None:
            self._device._decline_ticket(ticket_id, "inference proxy unavailable or returned empty")
            self._send(reply_to, {
                "kind": "dispatch_done",
                "ticket_id": ticket_id,
                "from_device": self._device_mailbox,
                "outcome": "decline",
            })
        else:
            should_esc, esc_reason = self._device._should_escalate(ticket, result)
            if should_esc:
                _task_outcome = "escalated"
                self._device._escalate_ticket(ticket_id, esc_reason, analysis=result)
                self._send(reply_to, {
                    "kind": "dispatch_done",
                    "ticket_id": ticket_id,
                    "from_device": self._device_mailbox,
                    "outcome": "escalated",
                })
            else:
                self._device._post_result(ticket_id, result)
                self._send(reply_to, {
                    "kind": "dispatch_done",
                    "ticket_id": ticket_id,
                    "from_device": self._device_mailbox,
                    "outcome": "done",
                })

        log.info("DickSimnel: task_outcome|ticket=%s|outcome=%s", ticket_id, _task_outcome)
        self._device._active_ticket = None
        self._last_active = time.monotonic()
