"""
CCWorkerShim — BaseShim for a CC (Claude Code) worker dispatch slot.

Manages the dispatch-facing infrastructure for one CC worker:
  - CCWorkerListener thread  (bus receive + Granny handshake, as background thread)
  - Granny announce registration   (~/.granny/announced/<worker_id>.json)
  - Availability flags             (~/.granny/available/<worker_id>.available.*)

The Claude Code app itself is NOT started here — CC is a human-interactive
tool that the operator runs in a tmux session. This shim manages what goes
around it so Granny can route tickets to it reliably.

Circuit breaker (BaseShim.check_circuit(), keyed by device_id):
  OPEN   → listener stays down (or is stopped if running); no new dispatches
  CLOSED → listener starts (or restarts); slot is dispatch-able

ensure_daemon_running() is bidirectional:
  OPEN  + listener alive  → stop()   (circuit was opened while listener ran)
  CLOSED + listener dead  → start()  (restart after crash or first run)

The rack supervisor calls ensure_daemon_running() every poll cycle so the
shim self-heals without any manual intervention.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from unseen_university.shim import BaseShim
from unseen_university.devices.granny.announce_worker import announce, withdraw

log = logging.getLogger(__name__)

_GRANNY_HOME = Path.home() / ".granny"
_AVAIL_DIR = _GRANNY_HOME / "available"


def _worker_id_to_mailbox(worker_id: str) -> str:
    """CC.1 → cc.1, CC.0 → cc.0."""
    parts = worker_id.split(".")
    if len(parts) == 2:
        return f"{parts[0].lower()}.{parts[1]}"
    return worker_id.lower()


class CCWorkerShim(BaseShim):
    """Shim for one CC worker dispatch slot (CC.0, CC.1, ...).

    One instance per slot. Instantiate with the slot's worker_id, then
    call start() or rely on the rack supervisor via ensure_daemon_running().

    Example:
        shim = CCWorkerShim("CC.1")
        shim.start()        # starts listener thread; circuit must be CLOSED
        shim.self_test()    # {"passed": True, "details": "..."}
        shim.stop()         # stops thread, withdraws announcement
    """

    def __init__(
        self,
        worker_id: str,
        *,
        mailbox: str | None = None,
        tmux_session: str | None = None,
        worker_name: str | None = None,
        granny_mailbox: str = "granny.0",
        one_at_a_time: bool = True,
        cascade_if_idle: bool = False,
    ) -> None:
        self._worker_id = worker_id
        self._mailbox = mailbox or _worker_id_to_mailbox(worker_id)
        self._tmux_session = tmux_session or self._default_tmux_session()
        self._worker_name = worker_name or (
            "claude" if worker_id == "CC.0" else self._mailbox
        )
        self._granny_mailbox = granny_mailbox
        self._one_at_a_time = one_at_a_time
        self._cascade_if_idle = cascade_if_idle
        self._listener = None  # CCWorkerListener instance when running

    def _default_tmux_session(self) -> str:
        import socket
        host = socket.gethostname().split(".")[0].lower()
        # tmux converts dots to underscores in session names — match convention.
        mailbox_safe = self._mailbox.replace(".", "_")
        return f"{host}_{mailbox_safe}"

    @property
    def device_id(self) -> str:
        return self._worker_id

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the CCWorkerListener thread and announce to Granny.

        No-ops if circuit is OPEN or listener thread is already alive.
        Returns True on success, False on failure.
        """
        if self.check_circuit():
            log.info("CCWorkerShim %s: circuit OPEN — not starting", self._worker_id)
            return False

        if self._listener_alive():
            log.debug("CCWorkerShim %s: listener thread already running", self._worker_id)
            return True

        try:
            from unseen_university.devices.bus.connection import make_bus_connection
            from unseen_university.devices.granny.cc_worker_listener import CCWorkerListener

            imap = make_bus_connection()
            self._listener = CCWorkerListener(
                imap=imap,
                cc_mailbox=self._mailbox,
                granny_mailbox=self._granny_mailbox,
                tmux_session=self._tmux_session,
            )
            self._listener.start()
        except Exception as exc:
            log.error(
                "CCWorkerShim %s: listener start failed: %s", self._worker_id, exc
            )
            self._listener = None
            return False

        announce(
            self._worker_id,
            mailbox=self._mailbox,
            worker_name=self._worker_name,
            one_at_a_time=self._one_at_a_time,
            cascade_if_idle=self._cascade_if_idle,
        )
        self._mark_available()
        self._post_status("listener", "started")
        log.info(
            "CCWorkerShim %s: listener thread started mailbox=%s session=%s",
            self._worker_id, self._mailbox, self._tmux_session,
        )
        return True

    def stop(self) -> bool:
        """Stop the listener thread, withdraw from Granny, mark unavailable."""
        self._mark_unavailable()
        withdraw(self._worker_id)
        self._cancel_active_handshakes()

        if self._listener is not None:
            try:
                self._listener.stop()
                log.info("CCWorkerShim %s: listener thread stopped", self._worker_id)
            except Exception as exc:
                log.warning(
                    "CCWorkerShim %s: listener stop error: %s", self._worker_id, exc
                )
            self._listener = None

        self._post_status("listener", "stopped")
        return True

    def restart(self) -> bool:
        self.stop()
        time.sleep(1.0)
        return self.start()

    def self_test(self) -> dict:
        """Check listener thread liveness and tmux session presence."""
        listener_ok = self._listener_alive()
        tmux_ok = self._tmux_session_exists()
        return {
            "passed": listener_ok,
            "details": (
                f"listener={'alive' if listener_ok else 'dead'} "
                f"tmux={'present' if tmux_ok else 'absent (CC not yet started)'} "
                f"mailbox={self._mailbox}"
            ),
        }

    def rollback(self) -> None:
        """Clean up after a failed start(). Idempotent."""
        withdraw(self._worker_id)
        self._mark_unavailable()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        log.info("CCWorkerShim %s: rollback complete", self._worker_id)

    def ensure_daemon_running(self) -> bool:
        """Watchdog hook: bidirectional reconcile between circuit state and listener.

        OPEN  + listener alive  → stop()  (circuit was opened while listener ran)
        CLOSED + listener dead  → start() (restart after crash or first run)
        Called by the rack supervisor on each poll cycle.
        """
        if self.check_circuit():
            if self._listener_alive():
                log.info(
                    "CCWorkerShim %s: circuit OPEN but listener alive — stopping",
                    self._worker_id,
                )
                self.stop()
            return True  # deliberately paused — not an error
        if not self._listener_alive():
            log.info(
                "CCWorkerShim %s: listener dead with circuit CLOSED — restarting",
                self._worker_id,
            )
            return self.start()
        return True

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _listener_alive(self) -> bool:
        """True if the listener thread is instantiated and running."""
        return self._listener is not None and self._listener.is_alive()

    def _tmux_session_exists(self) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", self._tmux_session],
            capture_output=True,
        )
        return result.returncode == 0

    def _mark_available(self) -> None:
        _AVAIL_DIR.mkdir(parents=True, exist_ok=True)
        (_AVAIL_DIR / f"{self._worker_id}.available.false").unlink(missing_ok=True)
        (_AVAIL_DIR / f"{self._worker_id}.available.true").touch()
        log.info("CCWorkerShim %s: marked available", self._worker_id)

    def _mark_unavailable(self) -> None:
        _AVAIL_DIR.mkdir(parents=True, exist_ok=True)
        (_AVAIL_DIR / f"{self._worker_id}.available.true").unlink(missing_ok=True)
        (_AVAIL_DIR / f"{self._worker_id}.available.false").touch()
        log.info("CCWorkerShim %s: marked unavailable", self._worker_id)
