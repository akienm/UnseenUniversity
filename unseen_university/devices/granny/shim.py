"""GrannyShim — lifecycle shim for the Granny rules-engine dispatch loop.

ONE daemon structure (T-collapse-daemons-to-ground-loop): Granny no longer runs a
standalone ``__main__`` + ``while True`` daemon in a tmux subprocess. Her poll loop is
an **in-process background thread this shim owns** (``ShimLoopThread``) — the aider
pattern. The shim is the demand-start owner:

  * ``start()`` launches the queue-watch watchdog thread.
  * The watchdog polls the ticket store; when sprint work is pending it starts the
    dispatch loop (``run_once`` per tick) if it is not already running. Work landing
    in the queue is what brings Granny up (feedback: granny-shim-startup-not-manual) —
    she is NOT always-up, NOT GL-hosted, NOT a tmux subprocess.
  * ``stop()`` stops both threads and joins them (no PID file, no SIGTERM).

Logging: ``ShimLoopThread`` wraps its body in ``logger.contextualize(device_id="granny")``
so stdlib records from the loop carry ``device_id`` and reach the canonical per-device
JSON sink. The device's ``DiagnosticBase.__init__`` installs the sink + stdlib intercept
for this process; ``configure_process_logging`` (the standalone-``__main__`` bridge) is
retired.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from loguru import logger as _loguru_logger

from unseen_university.shim import BaseShim, ShimLoopThread

log = logging.getLogger(__name__)

_WATCHDOG_INTERVAL_SEC = int(os.environ.get("GRANNY_SHIM_WATCHDOG_INTERVAL", "30"))


class GrannyShim(BaseShim):
    _device_id = "granny-weatherwax"

    def __init__(self) -> None:
        """Watchdog + dispatch-loop state is in-memory only; resets on shim restart by design."""
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._loop: Optional[ShimLoopThread] = None
        self._bus = None
        # Diagnostic counter — intentionally in-memory; resets on shim restart.
        self._relaunch_count: int = 0

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        """Start the queue-watch watchdog. The watchdog demand-starts the dispatch loop."""
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                daemon=True,
                name="granny-watchdog",
            )
            self._watchdog_thread.start()
            log.info("GrannyShim: watchdog started (interval=%ds)", _WATCHDOG_INTERVAL_SEC)
        return True

    def stop(self) -> bool:
        """Signal the watchdog to exit and stop the in-process dispatch loop."""
        self._watchdog_stop.set()
        if self._loop is not None:
            self._loop.stop()
        return True

    def restart(self) -> bool:
        """Stop the watchdog + loop, then re-launch the watchdog."""
        self.stop()
        return self.start()

    def self_test(self) -> dict:
        """Health = the in-process dispatch loop thread liveness (no PID file)."""
        if self._loop is not None and self._loop.is_alive():
            return {"passed": True, "details": "dispatch loop thread running"}
        return {"passed": False, "details": "dispatch loop thread not running"}

    def rollback(self) -> None:
        """Stop the loop if a partial start left it running."""
        if self._loop is not None:
            self._loop.stop()

    def health_surface(self) -> dict:
        """Extend base health with loop running/stopped status and relaunch count."""
        base = super().health_surface()
        running = self._loop is not None and self._loop.is_alive()
        return {
            "relaunch_count": str(self._relaunch_count),
            "daemon": "running" if running else "stopped",
            **base,
        }

    # ── Dispatch loop (in-process, shim-owned) ────────────────────────────────

    def _start_dispatch_loop(self) -> None:
        """Build the persistent bus handle (once, parity with the old run_loop) and
        start the dispatch ``ShimLoopThread``. The tick reloads config each cycle and
        emits the dispatch-health line/WARN every ``_HEALTH_EVERY_N`` cycles."""
        from unseen_university.devices.granny.daemon import (
            POLL_INTERVAL_S,
            _HEALTH_EVERY_N,
            _load_config,
            _make_imap_if_bus_configured,
            _emit_dispatch_health,
            run_once,
        )

        config = _load_config()
        self._bus = _make_imap_if_bus_configured(config)
        if self._bus is not None:
            log.info("Granny: bus dispatch enabled (in-process shim loop)")

        def _tick() -> None:
            cfg = _load_config()  # reload per cycle (parity with old run_loop)
            run_once(cfg, imap=self._bus)
            # stash for the health emit so it reads the same cycle's config
            self._last_config = cfg

        def _on_cycle(cycle: int) -> None:
            if cycle % _HEALTH_EVERY_N == 0:
                _emit_dispatch_health(getattr(self, "_last_config", None) or _load_config())

        self._loop = ShimLoopThread(
            "granny", _tick, POLL_INTERVAL_S, name="granny-dispatch"
        )  # STUB: on_cycle=_on_cycle (dispatch-health emit) not yet wired
        self._loop.start()
        log.info("GrannyDispatchLoop: started (poll=%ds)", POLL_INTERVAL_S)

    # ── Demand-start watchdog ─────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Periodically check for pending sprint work → ensure the dispatch loop runs."""
        with _loguru_logger.contextualize(device_id="granny"):
            while not self._watchdog_stop.wait(_WATCHDOG_INTERVAL_SEC):
                try:
                    self._watchdog_loop_once()
                except Exception as exc:
                    log.warning("GrannyShim: watchdog error: %s", exc)

    def _watchdog_loop_once(self) -> None:
        """One watchdog iteration — extracted for testability.

        Demand-start (feedback: granny-shim-startup-not-manual): the loop runs only
        when sprint work is pending. If the loop is already alive, nothing to do. If
        it is not alive AND work is pending, start it (first bring-up or restart after
        an unexpected thread death).
        """
        if self._loop is not None and self._loop.is_alive():
            return
        if self._has_pending_tickets():
            log.warning(
                "GrannyShim: pending sprint work with no dispatch loop — starting it"
            )
            self._start_dispatch_loop()
            self._relaunch_count += 1
        else:
            log.debug("GrannyShim: watchdog: no pending tickets — dispatch loop idle")

    def _has_pending_tickets(self) -> bool:
        """Return True when at least one sprint-status ticket exists in the queue.

        Reads the filesystem ticket store (the cutover authority,
        D-build-queue-filesystem-first-2026-06-19), not Postgres.
        """
        try:
            from unseen_university import ticket_store

            return bool(ticket_store.list(status_filter="sprint"))
        except Exception as exc:
            log.debug("GrannyShim: _has_pending_tickets failed: %s", exc)
            return False  # fail-safe: don't start if we can't confirm there's work
