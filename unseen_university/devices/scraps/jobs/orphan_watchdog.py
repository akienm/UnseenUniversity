"""
orphan_watchdog.py — Scraps job: detect and reset orphaned in_progress tickets.

A ticket is orphaned when it has been in_progress longer than its size-keyed
timeout with no resolution. Under the OR cascade, tickets run synchronously
inside the Granny daemon thread and complete in minutes; CC-dispatched tickets
have a tmux session. Either way, if the timeout has passed the session is gone.

Timeouts (conservative — should never fire on a healthy run):
  S  → 120 min   M  → 240 min   L  → 360 min   XL → 480 min

Emits: GRANNY_ORPHAN_RESET|ticket=<id>|age_minutes=<n>|size=<s>|reason=timeout
to the granny-weatherwax channel for each reset.

Run: python -m unseen_university.devices.scraps.jobs.orphan_watchdog
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
_CC_QUEUE = _UU_ROOT / "devlab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable

# Minutes before a ticket is considered orphaned by size class
_TIMEOUT_MINUTES: dict[str, int] = {
    "S": 120,
    "M": 240,
    "L": 360,
    "XL": 480,
}
_DEFAULT_TIMEOUT_MINUTES = 240


class OrphanWatchdog:
    """Detects and resets in_progress tickets whose timeout has elapsed."""

    def __init__(
        self,
        timeout_overrides: Optional[dict[str, int]] = None,
        db_url: Optional[str] = None,  # vestigial: ticket-state now reads the FS store
        p90_fn: Optional[callable] = None,
    ) -> None:
        self._timeouts = dict(_TIMEOUT_MINUTES)
        if timeout_overrides:
            self._timeouts.update(timeout_overrides)
        self._p90_fn = p90_fn  # callable(size) -> float|None

    def _load_in_progress(self) -> list[dict]:
        """Load all in_progress tickets from the filesystem ticket store.

        Filesystem-first (D-build-queue-filesystem-first-2026-06-19): ticket state
        is the filesystem queue, not clan.memories. ``list`` is lock-free (atomic
        files are always valid). Sort by dispatched_at ASC to match the old query
        (oldest-dispatched first), tolerating tickets that lack the field.
        """
        try:
            from unseen_university import ticket_store

            tickets = ticket_store.list(status_filter="in_progress")
            tickets.sort(key=lambda t: t.get("dispatched_at") or "")
            return tickets
        except Exception as e:
            log.warning("orphan_watchdog: failed to load in_progress tickets: %s", e)
            return []

    def _age_minutes(self, ticket: dict) -> Optional[float]:
        """Return minutes since dispatched_at, or None if field missing/unparseable."""
        dispatched_at = ticket.get("dispatched_at", "") or ticket.get("updated_at", "")
        if not dispatched_at:
            return None
        try:
            ts = datetime.fromisoformat(dispatched_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 60
        except Exception:
            return None

    def _timeout_for(self, ticket: dict) -> int:
        size = ticket.get("size", "")
        if self._p90_fn is not None:
            try:
                p90 = self._p90_fn(size)
                if p90 is not None:
                    calibrated = int(p90 * 3)
                    log.debug(
                        "orphan_watchdog: GRANNY_TIMEOUT_CALIBRATED|size=%s|p90=%.1fm|timeout=%dm",
                        size, p90, calibrated,
                    )
                    return calibrated
            except Exception as e:
                log.debug("orphan_watchdog: p90_fn failed for size=%s: %s", size, e)
        return self._timeouts.get(size, _DEFAULT_TIMEOUT_MINUTES)

    def _reset_ticket(self, ticket_id: str) -> bool:
        """Reset ticket to sprint via cc_queue.py. Returns True on success."""
        try:
            result = subprocess.run(
                [_PYTHON, str(_CC_QUEUE), "setstatus", ticket_id, "sprint"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception as e:
            log.warning("orphan_watchdog: setstatus failed for %s: %s", ticket_id, e)
            return False

    def _post_channel(self, msg: str) -> None:
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(
                msg, author="granny-weatherwax", channel="granny-weatherwax"
            )
        except Exception as e:
            log.warning("orphan_watchdog: channel post failed: %s", e)

    def run(self) -> list[str]:
        """Scan in_progress tickets; reset those past timeout. Returns reset ticket IDs."""
        tickets = self._load_in_progress()
        if not tickets:
            log.debug("orphan_watchdog: no in_progress tickets")
            return []

        reset: list[str] = []
        for ticket in tickets:
            tid = ticket.get("id", "")
            if not tid:
                continue

            age = self._age_minutes(ticket)
            if age is None:
                log.debug("orphan_watchdog: %s has no dispatched_at — skipping", tid)
                continue

            timeout = self._timeout_for(ticket)
            if age < timeout:
                log.debug(
                    "orphan_watchdog: %s age=%.0fm < timeout=%dm — ok",
                    tid,
                    age,
                    timeout,
                )
                continue

            size = ticket.get("size", "?")
            log.warning(
                "orphan_watchdog: %s age=%.0fm >= timeout=%dm size=%s — resetting",
                tid,
                age,
                timeout,
                size,
            )
            if self._reset_ticket(tid):
                msg = (
                    f"GRANNY_ORPHAN_RESET|ticket={tid}"
                    f"|age_minutes={age:.0f}|size={size}|timeout={timeout}m"
                    f"|reason=timeout"
                )
                self._post_channel(msg)
                reset.append(tid)

        if reset:
            log.info(
                "orphan_watchdog: reset %d orphaned ticket(s): %s", len(reset), reset
            )
        return reset


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    watchdog = OrphanWatchdog()
    reset = watchdog.run()
    print(f"Reset {len(reset)} orphaned ticket(s): {reset}")
    sys.exit(0)
