"""
dispatch.py — RETIRED CC-spawn dispatch path for GrannyWeatherwaxDevice.

These functions used to subprocess.Popen a
``claude --dangerously-skip-permissions -p /sprint-ticket <id>`` instance (and,
for the inference variant, run a MinionDevice tool loop and spawn CC on
escalate). They are NOT wired into the live daemon: granny/daemon.py routes every
dispatch through ``_dispatch_bus`` — a bus envelope to the target worker's shim
— per D-cc-shim-assignment-model-2026-06-06.

Spawning CC directly violates two standing rules:
  * feedback_granny_no_cc_spawn — Granny may *send-keys* to an existing CC
    session (under semaphore + usage gates) but must NEVER subprocess.Popen one;
    work over the limit ESCALATEs to hold, it does not fork a new CC.
  * feedback_cc_concurrency_hard_limit — one CC sprint at a time, period.

Leaving live spawn code here was a re-wiring footgun (T-granny-dispatch-deadcode-spawn):
anyone reconnecting dispatch.py to the daemon would silently re-introduce the
forbidden auto-spawn. So the functions are kept as loud tripwires — they raise
NotImplementedError pointing back at the bus model — rather than deleted, so the
mistake is caught at call time instead of slipping through.

The minion tool-loop logic that used to live in ``inference_dispatch_fn`` is
preserved in git history and, as the real execution home, in
``devices/minion/device.py`` (MinionDevice). The live cheap-inference path is the
bus envelope to the worker shim, not this glue.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_NEUTERED_MSG = (
    "granny.dispatch CC-spawn path is retired — dispatch goes through "
    "_dispatch_bus (bus envelope to the worker shim) per "
    "D-cc-shim-assignment-model-2026-06-06. Granny must NEVER subprocess-spawn a "
    "CC instance (feedback_granny_no_cc_spawn + feedback_cc_concurrency_hard_limit). "
    "If you hit this, you re-wired the dead path — route through the bus instead."
)


def _launch_cc_instance(ticket_id: str) -> None:
    """RETIRED — used to Popen a CC instance. Raises; route through the bus."""
    raise NotImplementedError(_NEUTERED_MSG)


def cc_dispatch_fn(ticket: dict) -> bool:
    """RETIRED — used to spawn CC for a ticket. Raises; route through the bus."""
    raise NotImplementedError(_NEUTERED_MSG)


def inference_dispatch_fn(ticket: dict) -> bool:
    """RETIRED — ran the minion loop + spawned CC on escalate. Raises.

    The live cheap-inference path is the bus envelope to the minion/worker shim;
    MinionDevice (devices/minion/device.py) remains the execution home.
    """
    raise NotImplementedError(_NEUTERED_MSG)
