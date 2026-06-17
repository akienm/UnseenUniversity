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
import threading

log = logging.getLogger(__name__)

_RETRY_DELAY_S = int(__import__("os").environ.get("CC_LISTENER_RETRY_DELAY", "30"))
_stop_evt = threading.Event()


def start() -> None:
    from devices.granny.cc_worker_listener import run_forever
    _stop_evt.clear()
    log.info("cc/groundloop/runme: starting CCWorkerListener")
    while not _stop_evt.is_set():
        try:
            run_forever()
            # run_forever() returns only when stop() signals it via SIGTERM/SIGINT
            log.info("cc/groundloop/runme: CCWorkerListener exited normally")
            break
        except Exception as exc:
            # IMAP/bus connection failures are transient — retry rather than crash.
            # Crashing here would cause RunmeSupervisor to rename us to .borkedpy.
            log.error(
                "cc/groundloop/runme: CCWorkerListener error: %s — retry in %ds",
                exc, _RETRY_DELAY_S,
            )
            _stop_evt.wait(_RETRY_DELAY_S)
    log.info("cc/groundloop/runme: stopped")


def stop() -> None:
    log.info("cc/groundloop/runme: stop called")
    _stop_evt.set()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
