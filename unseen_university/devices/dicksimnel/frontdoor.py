"""
DickSimnelFrontDoor — wake-triggered device launcher for DickSimnel.0.

Watches the dicksimnel.0 mailbox via idle_wait for incoming dispatch work.
On wake signal, ensures the DickSimnel device is running by spawning it if needed.
Never consumes the mailbox — idle_wait is only a trigger. The spawned device's
worker_listener is the sole consumer.

Availability flag lifecycle:
  - start() writes ~/.granny/available/DickSimnel.0.available.true (WAKEABLE)
  - stop() removes the flag
  - Front-door-spawned devices see UU_FRONTDOOR env var and skip flag management
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from unseen_university._uu_root import uu_home

log = logging.getLogger(__name__)


class DickSimnelFrontDoor:
    """
    Watches the bus for work and spawns DickSimnel.0 device on demand.

    The device runs in a subprocess. On each wake signal from the bus,
    checks if the device is still alive; if not, spawns a fresh instance.
    """

    def __init__(self) -> None:
        """Initialize front-door state. Constructs bus connection (fail-soft)."""
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._bus = self._connect_bus()

    def _connect_bus(self):
        """Return a bus connection for idle_wait, or None if unavailable."""
        try:
            from unseen_university.devices.bus.connection import make_bus_connection

            return make_bus_connection()
        except Exception as exc:
            log.warning(
                "DickSimnelFrontDoor: bus unavailable — front-door cannot wake device: %s", exc
            )
            return None

    def start(self) -> None:
        """Write availability flag, then enter run_forever loop.

        Called by runme.py start() in a daemon thread. Blocks in run_forever.
        """
        self._write_available()
        self.run_forever()

    def _write_available(self) -> None:
        """Write ~/.granny/available/DickSimnel.0.available.true to signal WAKEABLE."""
        flag_dir = Path.home() / ".granny" / "available"
        flag_dir.mkdir(parents=True, exist_ok=True)
        flag_path = flag_dir / "DickSimnel.0.available.true"
        flag_path.write_text("true")
        log.info("DickSimnelFrontDoor: availability flag written at %s", flag_path)

    def run_forever(self) -> None:
        """Main loop: watch bus for wakes, ensure device alive on each wake.

        Blocks until stop() is called (via stop event).
        Uses idle_wait(mailbox, timeout) as the wake mechanism.
        """
        mailbox = "dicksimnel.0"
        timeout_s = 25 * 60  # Default idle_wait timeout

        while not self._stop.is_set():
            if self._bus is None:
                # Bus unavailable: wait on stop event and retry
                if self._stop.wait(timeout=5):
                    break
                log.debug("DickSimnelFrontDoor: bus still unavailable, retrying...")
                continue

            try:
                # idle_wait is a wake trigger (has built-in unseen_count fast-path).
                # Returns True if work arrived (or was already waiting), False on timeout.
                if self._bus.idle_wait(mailbox, timeout_s=timeout_s):
                    self._ensure_device_awake()
            except Exception as exc:
                log.warning("DickSimnelFrontDoor: idle_wait error: %s", exc)
                # On error, sleep briefly before retry to avoid busy loop
                self._stop.wait(timeout=2)

    def _ensure_device_awake(self) -> None:
        """Ensure DickSimnel device is running. Spawn if down.

        Uses lock+double-check pattern to avoid concurrent spawns.
        """
        if self._device_alive():
            return

        with self._lock:
            # Double-check inside lock
            if self._device_alive():
                return

            self._spawn_device()

    def _device_alive(self) -> bool:
        """Check if device process is still alive."""
        return False  # STUB: liveness gate not yet implemented (proof red state)

    def _spawn_device(self) -> None:
        """Spawn DickSimnel.0 device subprocess with UU_FRONTDOOR env var set.

        Logs at INFO on wake and spawn; ERROR on spawn failure.
        """
        log.info("DickSimnelFrontDoor: wake received for DickSimnel.0")

        # Prepare log file for device output
        log_dir = Path(uu_home()) / "ground_loop" / "logs"
        log_path = log_dir / "dicksimnel.frontdoor-device.log"

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            device_log = open(log_path, "ab")
        except OSError as exc:
            log.warning(
                "DickSimnelFrontDoor: device_log_open_failed; using DEVNULL: %s", exc
            )
            device_log = subprocess.DEVNULL

        log.info(
            "DickSimnelFrontDoor: spawning device DickSimnel.0 at %s",
            log_path if device_log is not subprocess.DEVNULL else "DEVNULL",
        )

        env = {**os.environ, "UU_FRONTDOOR": "1"}

        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "unseen_university.devices.dicksimnel"],
                env=env,
                stdout=device_log,
                stderr=subprocess.STDOUT,
            )
            # Close our handle so we don't leak FDs (Popen dups the fd)
            if device_log is not subprocess.DEVNULL:
                device_log.close()
            log.info("DickSimnelFrontDoor: spawned device pid=%d", self._proc.pid)
        except Exception as exc:
            log.error("DickSimnelFrontDoor: spawn failed: %s", exc)
            if device_log is not subprocess.DEVNULL:
                device_log.close()
            # Leave _proc as-is; will retry on next wake

    def stop(self) -> None:
        """Signal loop to stop and remove availability flag."""
        log.info("DickSimnelFrontDoor: stop() called")
        self._stop.set()
        self._remove_available()

    def _remove_available(self) -> None:
        """Remove availability flag. FileNotFoundError is tolerant."""
        flag_path = Path.home() / ".granny" / "available" / "DickSimnel.0.available.true"
        try:
            flag_path.unlink()
            log.info("DickSimnelFrontDoor: availability flag removed")
        except FileNotFoundError:
            pass
