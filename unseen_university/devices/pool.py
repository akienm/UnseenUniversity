"""
InstancePool — reusable per-class free-list for worker instance leasing.

D-worker-pool-leasing-and-singleton-2026-07-02. Each worker class (e.g. "DickSimnel") maintains
a pool of instance slots — a free-list of integers (0, 1, 2, ...) that track live
process instances. The front-door shim uses this to assign reusable instance numbers
to spawned workers (so instance 0 crashes/exits → slot 0 becomes free → next spawn
reuses slot 0, not allocating slot 3).

Persistence: leases are written to a JSON file per class, e.g.
``~/.unseen_university/devices/DickSimnel/leases.json``. On restart, rebuild()
loads from the file and culls dead PIDs via an injected liveness check.

Liveness check (injectable for testing): Callable[[int, float|None], bool] —
(pid, create_time) -> True if alive, False if dead. The default uses os.kill
signal-0 trick plus optional create_time verification via psutil.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

_psutil_import_warned = False


def _default_liveness(pid: int, create_time: Optional[float]) -> bool:
    """
    Check if a process is alive by PID.

    Uses os.kill(pid, 0) to test process existence (signal 0 does not send a
    signal, only checks permissions). Returns True if alive, False if dead.
    If create_time is provided, also verifies it matches the process's actual
    create time (via psutil) to detect PID reuse. Tolerates small float epsilon.

    Args:
        pid: Process ID to check.
        create_time: Expected process create time (Unix timestamp), or None to skip check.

    Returns:
        True if process is alive and (if create_time given) has matching create_time.
        False if process is dead or create_time mismatch (likely PID reuse).
    """
    global _psutil_import_warned

    # Check if process exists via signal-0 trick
    try:
        os.kill(pid, 0)
        # Process exists (or we have permission to send it a signal)
    except ProcessLookupError:
        # Process does not exist
        return False
    except PermissionError:
        # Process exists but we don't have permission — assume alive
        return True

    # If no create_time to verify, we're done
    if create_time is None:
        return True

    # Verify create_time matches to catch PID reuse
    try:
        import psutil

        try:
            proc = psutil.Process(pid)
            actual_create_time = proc.create_time()
            # Allow small float epsilon for rounding differences
            epsilon = 1.0
            if abs(actual_create_time - create_time) < epsilon:
                return True
            else:
                # PID reuse: create_time mismatch
                return False
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            # Can't read create_time but process exists — assume alive
            return True
    except ImportError:
        # psutil not available; log once
        if not _psutil_import_warned:
            _psutil_import_warned = True
            log.warning(
                "pool: psutil not available; PID-reuse false-positives possible "
                "(create_time checks skipped)"
            )
        # Without psutil, we can't verify create_time; assume alive
        return True


def wipe_ephemeral_instance_dir(abbreviation: str, instance_number: int, home: Optional[str] = None) -> bool:
    """
    Wipe an ephemeral instance directory, resetting it to empty state.

    If instance_number == 0, returns False immediately (slot 0 is the durable
    foreground — NEVER wiped).

    Otherwise, resolves home (default uu_home()), computes the instance directory
    path, removes it entirely, recreates it empty, logs at INFO, and returns True.

    Args:
        abbreviation: Device abbreviation (e.g., "DS" for DickSimnel).
        instance_number: Instance slot number.
        home: Runtime data dir root. Defaults to uu_home().

    Returns:
        True if wiped, False if slot 0 or not applicable.
    """
    if instance_number == 0:
        return False
    return False  # STUB: ephemeral wipe not implemented yet (proof red state)

    if home is None:
        from unseen_university._uu_root import uu_home

        home = uu_home()

    d = Path(home) / "devices" / f"{abbreviation}.{instance_number}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    log.info("startup: wiped %s.%d (ephemeral)", abbreviation, instance_number)
    return True


class InstancePool:
    """
    Free-list manager for worker instance slots.

    Maintains a per-class pool of instance numbers (0, 1, 2, ...). Each slot
    holds a dict with pid, create_time, and handle (Popen). Persistence is via
    a leases.json file (pid + create_time only; handle is never persisted).

    Constructor does NOT auto-load — caller must call rebuild() explicitly so
    pools start clean for unit tests.

    Args:
        class_name: Full class name, e.g. "DickSimnel". Used in log messages and
                    to compose the leases.json path.
        liveness: Optional callable(pid, create_time) -> bool for liveness checks.
                  Defaults to _default_liveness. Useful for testing.
        home: Runtime data dir root. Defaults to uu_home(). Leases file lives at
              home/devices/<class_name>/leases.json.
    """

    def __init__(
        self,
        class_name: str,
        *,
        liveness: Optional[Callable[[int, Optional[float]], bool]] = None,
        home: Optional[str] = None,
        max_instances: Optional[int] = None,
    ) -> None:
        self._class_name = class_name
        self._liveness = liveness or _default_liveness
        self._max_instances = max_instances

        if home is None:
            from unseen_university._uu_root import uu_home

            home = uu_home()

        self._home = Path(home)
        self._leases_path = self._home / "devices" / class_name / "leases.json"

        # Start with empty slots — rebuild() is called explicitly by the caller
        self._slots: list[Optional[dict]] = []

    def first_free(self) -> int:
        """
        Return the index of the first free (None) slot, or len(self._slots) if all taken.

        Non-mutating; used to decide where the next allocate() should place a slot.
        """
        for i, slot in enumerate(self._slots):
            if slot is None:
                return i
        return len(self._slots)

    def allocate(self, pid: int, create_time: Optional[float] = None, handle=None) -> Optional[int]:
        """
        Allocate a slot for a new process instance.

        Finds the first free slot (or appends if none free), stores the pid/create_time/handle,
        persists leases.json, and logs at INFO.

        Args:
            pid: Process ID.
            create_time: Process creation timestamp (Unix time), or None.
            handle: Popen handle, or None. Never persisted.

        Returns:
            The slot index (instance number), or None if max_instances is set and capacity is reached.
        """
        idx = self.first_free()
        # STUB: capacity cap not enforced yet (proof red state)
        slot = {"pid": pid, "create_time": create_time, "handle": handle}

        if idx == len(self._slots):
            self._slots.append(slot)
        else:
            self._slots[idx] = slot

        self._persist()
        log.info("lease: assigned %s.%d", self._class_name, idx)
        return idx

    def release(self, n: int) -> None:
        """
        Release a slot, freeing it for reuse.

        Sets slot n to None. If n is the last slot (or one of the trailing Nones),
        trims all trailing Nones and persists.

        Args:
            n: Slot index to release.
        """
        if n < len(self._slots):
            self._slots[n] = None

        # Trim trailing Nones
        while self._slots and self._slots[-1] is None:
            self._slots.pop()

        self._persist()
        log.info("lease: released %s.%d", self._class_name, n)

    def rebuild(self) -> None:
        """
        Load leases.json and rebuild the slot list.

        Reads the leases file (missing/empty -> empty list, no error). For each
        slot, checks liveness via self._liveness(pid, create_time) — dead slots
        become None. Trims trailing Nones and persists. Popen handles are always
        None after rebuild (they're never persisted).
        """
        self._slots = []

        # Load leases.json if it exists
        if not self._leases_path.exists():
            return

        try:
            data = json.loads(self._leases_path.read_text())
            if not data:
                return
        except (json.JSONDecodeError, OSError):
            log.warning("pool: failed to read leases.json at %s", self._leases_path)
            return

        # Rebuild slots from persisted data
        for entry in data:
            if entry is None:
                self._slots.append(None)
            else:
                pid = entry.get("pid")
                create_time = entry.get("create_time")

                # Check liveness
                if self._liveness(pid, create_time):
                    # Restore with handle=None (handles are never persisted)
                    self._slots.append({"pid": pid, "create_time": create_time, "handle": None})
                else:
                    self._slots.append(None)

        # Trim trailing Nones and persist
        while self._slots and self._slots[-1] is None:
            self._slots.pop()

        self._persist()

    def taken(self) -> list[int]:
        """
        Return a list of indices of all non-None (live) slots.

        Useful for checking if the pool has any active instances.
        """
        return [i for i, slot in enumerate(self._slots) if slot is not None]

    def _persist(self) -> None:
        """Write leases.json to disk (pid + create_time only; no Popen handles)."""
        # Ensure parent directory exists
        self._leases_path.parent.mkdir(parents=True, exist_ok=True)

        # Build persisted data (only pid + create_time, not handle)
        data = []
        for slot in self._slots:
            if slot is None:
                data.append(None)
            else:
                data.append({"pid": slot["pid"], "create_time": slot["create_time"]})

        # Write atomically (well, as atomically as possible with JSON)
        self._leases_path.write_text(json.dumps(data, indent=2))
