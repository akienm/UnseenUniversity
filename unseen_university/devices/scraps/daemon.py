"""
daemon.py — Scraps job-runner tick.

ONE daemon structure (T-collapse-daemons-to-ground-loop): the standalone ``run_loop`` +
``__main__`` + PID file + signal handlers, and the Ground-Loop ``PluginDaemon`` subprocess
that spawned them (the retired ``config/ground_loop/scraps.yaml``), are GONE. This module
now provides ONLY the per-tick body ``run_once`` and the job registry — the loop that
calls ``run_once`` is an in-process, shim-owned thread (``ScrapsShim`` → ``ShimLoopThread``).
Bring Scraps up with: ``python -m unseen_university.devices.scraps``.

JOBS is an EXPLICIT registry, NOT a scan of devices/scraps/jobs/. Several modules in that
directory have side effects that must not be flipped on implicitly:
  - orphan_watchdog resets in_progress tickets (could yank live work from DS.0)
  - inference_outcome_learner does inference (OpenRouter/Haiku budget)
Enabling each additional job is a separate, deliberately-verified decision.
"""

from __future__ import annotations

import logging
import os

from unseen_university.devices.scraps.jobs.stale_chat_log_backfiller import StaleChatLogBackfiller

log = logging.getLogger(__name__)

# Poll cadence of the runner itself. Jobs self-gate on their own
# REFRESH_INTERVAL_SEC, so this only needs to be fine-grained enough to honour
# the shortest job interval (backfiller = 300s).
POLL_INTERVAL_S = int(os.environ.get("SCRAPS_POLL_INTERVAL", "30"))

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
