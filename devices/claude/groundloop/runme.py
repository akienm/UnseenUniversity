"""
CC.0 Ground Loop daemon — listens on cc.0 mailbox for Granny dispatch envelopes.

When a dispatch arrives:
  1. Acks receipt to Granny immediately.
  2. Appends "CC.0 acked at <timestamp>" note to the ticket.
  3. Posts a soft tmux nudge ("\r\r\rcheck messages when possible\n") into
     the CC tmux session. DOES NOT inject /sprint-ticket — CC decides whether
     and when to pick up the ticket (feedback_granny_no_cc_spawn; ca433bd7).
  4. Starts a nag thread: if the ticket is still not in_progress after
     CC_SHIM_NAG_INTERVAL (default 600s), sends another soft nudge.
     Stops when the ticket reaches a terminal status.

Nag state is persisted to ~/.granny/nag_state/ so restarts resume cleanly.
"""

import logging
import os

log = logging.getLogger(__name__)


def start() -> None:
    from devices.granny.cc_worker_listener import run_forever
    log.info("cc/groundloop/runme: starting CCWorkerListener")
    run_forever()


def stop() -> None:
    # run_forever() handles SIGTERM itself; stop() is a no-op here
    log.info("cc/groundloop/runme: stop called (SIGTERM handles cleanup)")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
