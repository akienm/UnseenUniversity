"""
DickSimnel Front-Door — Ground Loop daemon.

Manages on-demand dispatch listening for DickSimnel.0 via the DickSimnelFrontDoor.
The front-door watches the dicksimnel.0 mailbox and spawns the device on incoming
work, ensuring it stays alive across restarts and clean shutdown on signal.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_stop_evt = threading.Event()
_front_door = None


def start() -> None:
    """Start the front-door listener in a blocking loop."""
    from unseen_university.devices.dicksimnel.frontdoor import DickSimnelFrontDoor

    global _front_door

    _stop_evt.clear()
    _front_door = DickSimnelFrontDoor()
    log.info("dicksimnel/groundloop/runme: front-door created")

    try:
        _front_door.start()
    except Exception as exc:
        log.warning("dicksimnel/groundloop/runme: tick error: %s", exc)

    log.info("dicksimnel/groundloop/runme: stop signal received — shutting down")


def stop() -> None:
    """Signal the front-door to stop."""
    log.info("dicksimnel/groundloop/runme: stop() called")
    _stop_evt.set()
    if _front_door is not None:
        try:
            _front_door.stop()
            log.info("dicksimnel/groundloop/runme: front-door stopped")
        except Exception as exc:
            log.warning("dicksimnel/groundloop/runme: stop error: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
