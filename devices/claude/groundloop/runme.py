"""
CC worker slots — Ground Loop daemon.

Manages dispatch listener threads for CC.0 and CC.1 via CCWorkerShim.
Each slot checks its own circuit breaker every POLL_INTERVAL seconds:
  CLOSED → ensure listener thread is running (start if dead)
  OPEN   → ensure listener thread is stopped (stop if alive)

Toggle via web UI /devices page or write ~/.unseen_university/circuit_state.json.
CC.1 starts OPEN by default until the operator enables it via the web UI.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_POLL_S = 30
_SLOTS = ["CC.0", "CC.1"]
_stop_evt = threading.Event()
_shims: dict = {}


def start() -> None:
    from devices.claude.worker_shim import CCWorkerShim

    _stop_evt.clear()
    _shims.clear()
    for slot_id in _SLOTS:
        _shims[slot_id] = CCWorkerShim(slot_id)
        log.info("cc/groundloop/runme: registered slot=%s", slot_id)

    log.info("cc/groundloop/runme: polling %d slot(s) every %ds", len(_shims), _POLL_S)
    while not _stop_evt.is_set():
        for slot_id, shim in list(_shims.items()):
            try:
                shim.ensure_daemon_running()
            except Exception as exc:
                log.warning("cc/groundloop/runme: tick error slot=%s: %s", slot_id, exc)
        _stop_evt.wait(_POLL_S)

    log.info("cc/groundloop/runme: stop signal — shutting down slots")
    for slot_id, shim in list(_shims.items()):
        try:
            shim.stop()
            log.info("cc/groundloop/runme: stopped slot=%s", slot_id)
        except Exception as exc:
            log.warning("cc/groundloop/runme: stop error slot=%s: %s", slot_id, exc)


def stop() -> None:
    log.info("cc/groundloop/runme: stop() called")
    _stop_evt.set()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
