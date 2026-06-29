"""
demo_daemon — Ground Loop supervisor smoke test.

Writes a heartbeat line to logs/demo_daemon/heartbeat.log every
HEARTBEAT_INTERVAL seconds. Designed to be the simplest possible production-
shaped daemon: start() blocks until stop() is called, hot-reload works, and
an injected import/runtime error produces a .borkedpy rename.

To verify manually:
  1. Run Ground Loop (--once) in the repo root.
  2. Check logs/demo_daemon/heartbeat.log grows.
  3. Modify this file (touch it) and run --once again → hot-reload.
  4. Temporarily break this file (syntax error) → .borkedpy rename.
  5. Fix and rename back → recovery on next --once.

AR-009: logs every state transition (start, stop, each heartbeat).
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = int(os.environ.get("DEMO_DAEMON_INTERVAL", "5"))
_LOG_DIR = (
    Path(uu_home())
    / "logs"
    / "demo_daemon"
)
_HEARTBEAT_LOG = _LOG_DIR / "heartbeat.log"

_stop_event = threading.Event()


def start() -> None:
    """Entry point called by RunmeSupervisor in a daemon thread."""
    _stop_event.clear()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.info("DEMO_DAEMON|action=start|interval=%ds|log=%s", HEARTBEAT_INTERVAL, _HEARTBEAT_LOG)
    count = 0
    while not _stop_event.wait(timeout=HEARTBEAT_INTERVAL):
        count += 1
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts} heartbeat #{count}\n"
        try:
            with open(_HEARTBEAT_LOG, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            log.warning("DEMO_DAEMON|action=write_failed|exc=%s", exc)
        log.info("DEMO_DAEMON|action=heartbeat|count=%d", count)
    log.info("DEMO_DAEMON|action=stop|heartbeats=%d", count)


def stop() -> None:
    """Signal start() to exit cleanly."""
    log.info("DEMO_DAEMON|action=stop_requested")
    _stop_event.set()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
