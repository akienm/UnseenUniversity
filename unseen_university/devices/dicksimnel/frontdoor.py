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
from unseen_university.devices.pool import InstancePool
from unseen_university.devices.dicksimnel.consts import MAX_INSTANCES

log = logging.getLogger(__name__)

IDLE_POLL_INTERVAL_S = float(os.environ.get("DICKSIMNEL_IDLE_POLL_INTERVAL_S", "120"))
IDLE_TIMEOUT_S = float(os.environ.get("DICKSIMNEL_IDLE_TIMEOUT_S", "120"))


class DickSimnelFrontDoor:
    """
    Watches the bus for work and spawns DickSimnel.0 device on demand.

    The device runs in a subprocess. On each wake signal from the bus,
    checks if the device is still alive; if not, spawns a fresh instance.
    """

    def __init__(self) -> None:
        """Initialize front-door state. Constructs bus connection (fail-soft)."""
        self._pool = InstancePool("DickSimnel", max_instances=MAX_INSTANCES)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._bus = self._connect_bus()
        self._sweeper_thread: threading.Thread | None = None

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
        """Write availability flag, start sweeper thread, then enter run_forever loop.

        Called by runme.py start() in a daemon thread. Blocks in run_forever.
        """
        self._write_available()
        self._pool.rebuild()
        # Start the idle sweeper thread (daemon)
        self._sweeper_thread = threading.Thread(
            target=self._run_idle_sweeper, daemon=True, name="dicksimnel-idle-sweeper"
        )
        self._sweeper_thread.start()
        log.info("DickSimnelFrontDoor: idle sweeper thread started")
        # Now enter main loop
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

            # Get the first free slot
            n = self._pool.first_free()

            # Spawn the device subprocess
            proc = self._spawn_device(n)
            if proc is None:
                # Spawn failed; will retry on next wake
                return

            # Determine create_time from the spawned process (lazy psutil)
            create_time = self._get_create_time(proc)

            # Create instance directory
            instance_dir = Path(uu_home()) / "devices" / f"DS.{n}"
            try:
                instance_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning(
                    "DickSimnelFrontDoor: failed to mkdir instance dir %s: %s",
                    instance_dir,
                    exc,
                )

            # Allocate to the pool
            alloc_result = self._pool.allocate(pid=proc.pid, create_time=create_time, handle=proc)
            if alloc_result is None:
                log.warning("DickSimnelFrontDoor: pool at capacity — not recording spawn")
                return

    def _device_alive(self) -> bool:
        """Check if device process is still alive.

        Returns True if the pool has any taken (live) slots.
        """
        return bool(self._pool.taken())

    def _spawn_device(self, n: int) -> Optional[subprocess.Popen]:
        """Spawn DickSimnel.{n} device subprocess with UU_FRONTDOOR and UU_INSTANCE_NUMBER env vars set.

        Logs at INFO on wake and spawn; ERROR on spawn failure.
        Returns the Popen handle on success, or None on failure.
        """
        log.info("DickSimnelFrontDoor: wake received for DickSimnel.%d", n)

        # Prepare log file for device output
        log_dir = Path(uu_home()) / "ground_loop" / "logs"
        log_path = log_dir / f"dicksimnel.frontdoor-device-{n}.log"

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            device_log = open(log_path, "ab")
        except OSError as exc:
            log.warning(
                "DickSimnelFrontDoor: device_log_open_failed; using DEVNULL: %s", exc
            )
            device_log = subprocess.DEVNULL

        log.info(
            "DickSimnelFrontDoor: spawning device DickSimnel.%d at %s",
            n,
            log_path if device_log is not subprocess.DEVNULL else "DEVNULL",
        )

        env = {**os.environ, "UU_FRONTDOOR": "1", "UU_INSTANCE_NUMBER": str(n)}

        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "unseen_university.devices.dicksimnel"],
                env=env,
                stdout=device_log,
                stderr=subprocess.STDOUT,
            )
            # Close our handle so we don't leak FDs (Popen dups the fd)
            if device_log is not subprocess.DEVNULL:
                device_log.close()
            log.info("DickSimnelFrontDoor: spawned device pid=%d", proc.pid)
            return proc
        except Exception as exc:
            log.error("DickSimnelFrontDoor: spawn failed: %s", exc)
            if device_log is not subprocess.DEVNULL:
                device_log.close()
            # Return None to signal failure; will retry on next wake
            return None

    def _get_create_time(self, proc: subprocess.Popen) -> Optional[float]:
        """Get the process creation time from psutil, if available.

        Returns the Unix timestamp of process creation, or None if psutil is unavailable
        or the process info cannot be read.
        """
        try:
            import psutil

            try:
                p = psutil.Process(proc.pid)
                return p.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
        except ImportError:
            return None

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

    def _run_idle_sweeper(self) -> None:
        """Idle sweeper thread loop: periodically fire quit_if_idle to all live instances."""
        while not self._stop.is_set():
            try:
                self._idle_sweep()
            except Exception as exc:
                log.warning("DickSimnelFrontDoor: idle_sweep error: %s", exc)
            # Wait on stop event with timeout (so loop can exit promptly)
            self._stop.wait(timeout=IDLE_POLL_INTERVAL_S)

    def _idle_sweep(self) -> None:
        """Send quit_if_idle to all live instances and cull dead processes."""
        if self._bus is None:
            return

        from unseen_university.devices.bus.envelope import Envelope

        # Send quit_if_idle to each live instance
        for n in self._pool.taken():
            try:
                env = Envelope.now(
                    from_device="dicksimnel-frontdoor",
                    to_device=f"dicksimnel.{n}",
                    payload={"kind": "quit_if_idle", "idle_timeout": IDLE_TIMEOUT_S},
                )
                self._bus.append(f"dicksimnel.{n}", env)
                log.info("DickSimnelFrontDoor: quit_if_idle sent to dicksimnel.%d", n)
            except Exception as exc:
                log.warning("DickSimnelFrontDoor: failed to send quit_if_idle to dicksimnel.%d: %s", n, exc)

        # Cull dead processes from pool
        reclaimed = self._pool.cull_dead()
        if reclaimed:
            log.info("DickSimnelFrontDoor: culled dead instances: %s", reclaimed)
