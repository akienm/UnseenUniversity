"""
daemon.py — Scraps job-runner daemon.

The single long-running process for Scraps maintenance jobs. The Ground Loop
supervises it (config/ground_loop/scraps.yaml, mode: daemon) — so these jobs run
independently of whether Igor's cognition is up. This is the "one daemon loop"
shape: Ground Loop is the supervisor; this process ticks the registered jobs;
each job self-gates on its own REFRESH_INTERVAL_SEC.

Mirrors devices/granny/daemon.py (run_loop + __main__ signal handling).

JOBS is an EXPLICIT registry, NOT a scan of devices/scraps/jobs/. Several modules
in that directory have side effects that must not be flipped on implicitly:
  - orphan_watchdog resets in_progress tickets (could yank live work from DS.0)
  - inference_outcome_learner does inference (OpenRouter/Haiku budget)
Enabling each additional job is a separate, deliberately-verified decision.
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import logging
import os
import signal
import sys
import time
from pathlib import Path

from unseen_university.devices.scraps.jobs.stale_chat_log_backfiller import StaleChatLogBackfiller

log = logging.getLogger(__name__)

# Poll cadence of the runner itself. Jobs self-gate on their own
# REFRESH_INTERVAL_SEC, so this only needs to be fine-grained enough to honour
# the shortest job interval (backfiller = 300s).
POLL_INTERVAL_S = int(os.environ.get("SCRAPS_POLL_INTERVAL", "30"))

_SCRAPS_HOME = Path(uu_home()) / "scraps"
_PID_FILE = _SCRAPS_HOME / "daemon.pid"

# Explicit job registry — see module docstring on why this is not a directory scan.
JOBS = [
    StaleChatLogBackfiller(),
]


def run_once() -> None:
    """Tick every registered job once; each job self-gates on its interval."""
    for job in JOBS:
        try:
            job.run()
        except Exception as e:  # one bad job must not kill the loop
            log.error("Scraps: job %s error: %s", getattr(job, "name", job), e)


def run_loop(once: bool = False) -> None:
    log.info(
        "Scraps: job-runner daemon starting (poll=%ds, jobs=%s)",
        POLL_INTERVAL_S,
        [getattr(j, "name", type(j).__name__) for j in JOBS],
    )
    _SCRAPS_HOME.mkdir(parents=True, exist_ok=True)
    cycle = 0
    while True:
        cycle += 1
        log.debug("Scraps: poll cycle %d", cycle)
        run_once()
        if once:
            return
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _SCRAPS_HOME.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))

    def _handle_sig(sig, _frame):
        log.info("Scraps: signal %s — exiting", sig)
        _PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    once = "--once" in sys.argv
    try:
        run_loop(once=once)
    finally:
        _PID_FILE.unlink(missing_ok=True)
