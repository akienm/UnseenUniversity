"""
CCWorkerShim — BaseShim for a CC (Claude Code) worker dispatch slot.

Manages the dispatch-facing infrastructure for one CC worker:
  - cc_worker_listener subprocess  (bus receive + Granny handshake)
  - Granny announce registration   (~/.granny/announced/<worker_id>.json)
  - Availability flags             (~/.granny/available/<worker_id>.available.*)

The Claude Code app itself is NOT started here — CC is a human-interactive
tool that the operator runs in a tmux session. This shim manages what goes
around it so Granny can route tickets to it reliably.

Circuit breaker (BaseShim.check_circuit(), keyed by device_id):
  OPEN   → listener stays down; no new dispatches accepted
  CLOSED → listener starts (or restarts); slot is dispatch-able

"Closing the switch" == removing or setting the circuit entry to CLOSED.
The rack's watchdog calls ensure_daemon_running() each poll; the first
closed poll starts the listener, which announces and makes the slot live.

Concurrency note: CC.0 and CC.1 each get a separate listener pid file
so their pid files never collide.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from unseen_university.shim import BaseShim
from devices.granny.announce_worker import announce, is_alive, withdraw

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).resolve().parents[2]
_GRANNY_HOME = Path.home() / ".granny"
_AVAIL_DIR = _GRANNY_HOME / "available"


def _worker_id_to_mailbox(worker_id: str) -> str:
    """CC.1 → cc.1, CC.0 → cc.0."""
    parts = worker_id.split(".")
    if len(parts) == 2:
        return f"{parts[0].lower()}.{parts[1]}"
    return worker_id.lower()


def _listener_pid_file(mailbox: str) -> Path:
    """Per-slot pid file so CC.0 and CC.1 never collide."""
    safe = mailbox.replace(".", "_")
    return _GRANNY_HOME / f"cc_listener_{safe}.pid"


class CCWorkerShim(BaseShim):
    """Shim for one CC worker dispatch slot (CC.0, CC.1, ...).

    One instance per slot. Instantiate with the slot's worker_id, then
    call start() or rely on the rack's watchdog via ensure_daemon_running().

    Example:
        shim = CCWorkerShim("CC.1")
        shim.start()        # starts listener; circuit must be CLOSED
        shim.self_test()    # {"passed": True, "details": "..."}
        shim.stop()         # stops listener, withdraws announcement
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
        self._pid_file = _listener_pid_file(self._mailbox)

    def _default_tmux_session(self) -> str:
        import socket
        host = socket.gethostname().split(".")[0].lower()
        return f"{host}.{self._mailbox}"

    @property
    def device_id(self) -> str:
        return self._worker_id

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the cc_worker_listener subprocess and announce to Granny.

        No-ops if circuit is OPEN or listener is already alive.
        Returns True on success, False on failure.
        """
        if self.check_circuit():
            log.info("CCWorkerShim %s: circuit OPEN — not starting", self._worker_id)
            return False

        if self._listener_alive():
            log.debug("CCWorkerShim %s: listener already running", self._worker_id)
            return True

        env = {
            **os.environ,
            "CC_MAILBOX": self._mailbox,
            "GRANNY_MAILBOX": self._granny_mailbox,
            "CC_TMUX_SESSION": self._tmux_session,
            "CC_LISTENER_PID_FILE": str(self._pid_file),
        }
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "devices.granny.cc_worker_listener"],
                env=env,
                cwd=str(_UU_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Brief pause so the listener writes its pid file + announce file
            time.sleep(0.5)
            log.info(
                "CCWorkerShim %s: listener started (pid=%d mailbox=%s session=%s)",
                self._worker_id, proc.pid, self._mailbox, self._tmux_session,
            )
            self._mark_available()
            self._post_status("listener", "started")
            return True
        except Exception as exc:
            log.error("CCWorkerShim %s: listener start failed: %s", self._worker_id, exc)
            return False

    def stop(self) -> bool:
        """Stop the listener, withdraw from Granny, mark unavailable."""
        self._mark_unavailable()
        withdraw(self._worker_id)
        self._cancel_active_handshakes()

        pid = self._read_pid_file()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                log.info(
                    "CCWorkerShim %s: SIGTERM sent to listener pid=%d",
                    self._worker_id, pid,
                )
            except ProcessLookupError:
                pass
            except Exception as exc:
                log.warning("CCWorkerShim %s: stop error: %s", self._worker_id, exc)

        self._post_status("listener", "stopped")
        return True

    def restart(self) -> bool:
        self.stop()
        time.sleep(1.0)
        return self.start()

    def self_test(self) -> dict:
        """Check listener liveness and tmux session presence."""
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
        pid = self._read_pid_file()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        log.info("CCWorkerShim %s: rollback complete", self._worker_id)

    def ensure_daemon_running(self) -> bool:
        """Watchdog hook: restart listener when circuit is CLOSED and listener is dead.

        Called by the rack on each poll cycle. Returning True means the device
        is in the expected state (either paused by circuit or running normally).
        """
        if self.check_circuit():
            return True  # deliberately paused — not an error
        if not self._listener_alive():
            log.info(
                "CCWorkerShim %s: listener dead with circuit CLOSED — restarting",
                self._worker_id,
            )
            return self.start()
        return True

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _read_pid_file(self) -> int | None:
        """Read pid from the per-slot listener pid file."""
        try:
            return int(self._pid_file.read_text().strip())
        except Exception:
            return None

    def _listener_alive(self) -> bool:
        """True if the listener pid (from pid file or announce file) is running."""
        pid = self._read_pid_file()
        if pid and is_alive(pid):
            return True
        # Fallback: check announce file (pid=0 means manually managed → treat as alive)
        announce_path = _GRANNY_HOME / "announced" / f"{self._worker_id}.json"
        try:
            rec = json.loads(announce_path.read_text())
            pid2 = rec.get("pid", 0)
            return is_alive(pid2)
        except Exception:
            return False

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
