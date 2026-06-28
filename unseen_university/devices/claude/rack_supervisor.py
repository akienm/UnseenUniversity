"""
devices/claude/rack_supervisor.py — CC dispatch slot supervisor.

Holds one CCWorkerShim per configured dispatch slot and calls
ensure_daemon_running() every RACK_POLL_INTERVAL seconds. Designed to
run under Ground Loop supervision; at startup it deposits a YAML descriptor
so Ground Loop can restart it on crash.

Configuration (env vars):
  RACK_SLOTS         — comma-separated slot IDs (default: CC.0,CC.1)
  RACK_POLL_INTERVAL — watchdog poll interval in seconds (default: 30)
  IGOR_HOME          — runtime state dir (default: ~/.unseen_university)

Excluded by default: DS.0 (DickSimnel shim not yet implemented).
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import logging
import os
import signal
import sys
import threading
from pathlib import Path

import yaml

from unseen_university.devices.claude.worker_shim import CCWorkerShim

log = logging.getLogger(__name__)

_IGOR_HOME = Path(uu_home())
_PLUGIN_DIR = _IGOR_HOME / "ground_loop"
_DEFAULT_SLOTS = [
    s.strip()
    for s in os.environ.get("RACK_SLOTS", "CC.0,CC.1").split(",")
    if s.strip()
]
_DEFAULT_POLL = int(os.environ.get("RACK_POLL_INTERVAL", "30"))


class RackSupervisor:
    """Holds CCWorkerShim instances for CC dispatch slots and ticks their watchdogs.

    Usage:
        sup = RackSupervisor()
        sup.start()          # registers shims + deposits Ground Loop descriptor
        sup.run_forever()    # blocks; calls ensure_daemon_running() each cycle
        sup.stop()           # callable from signal handler; preempts run_forever()
    """

    def __init__(
        self,
        slots: list[str] | None = None,
        poll_interval: int = _DEFAULT_POLL,
        plugin_dir: Path = _PLUGIN_DIR,
    ) -> None:
        self._slots = slots if slots is not None else list(_DEFAULT_SLOTS)
        self._poll_interval = poll_interval
        self._plugin_dir = plugin_dir
        self._shims: dict[str, CCWorkerShim] = {}
        self._stop = threading.Event()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Register shims for all configured slots and deposit Ground Loop descriptor."""
        for slot_id in self._slots:
            self._shims[slot_id] = CCWorkerShim(slot_id)
            log.info("rack_supervisor: registered slot=%s", slot_id)
        self._deposit_descriptor()
        log.info(
            "rack_supervisor: start complete slots=%s poll=%ds",
            list(self._shims),
            self._poll_interval,
        )

    def run_forever(self) -> None:
        """Block and tick watchdog until stop() is called."""
        self._stop.clear()
        log.info("rack_supervisor: entering run loop poll=%ds", self._poll_interval)
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(timeout=self._poll_interval)
        self._shutdown()

    def stop(self) -> None:
        """Signal run_forever() to exit (safe to call from a signal handler)."""
        log.info("rack_supervisor: stop requested")
        self._stop.set()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Call ensure_daemon_running() on each shim. Errors are isolated per slot."""
        for slot_id, shim in list(self._shims.items()):
            try:
                log.debug("rack_supervisor: tick slot=%s", slot_id)
                ok = shim.ensure_daemon_running()
                if not ok:
                    log.warning(
                        "rack_supervisor: ensure_daemon_running=False slot=%s", slot_id
                    )
            except Exception as exc:
                log.warning(
                    "rack_supervisor: tick error slot=%s exc=%s", slot_id, exc
                )

    def _shutdown(self) -> None:
        """Stop all shims on exit."""
        log.info("rack_supervisor: shutting down %d slot(s)", len(self._shims))
        for slot_id, shim in self._shims.items():
            try:
                shim.stop()
                log.info("rack_supervisor: stopped slot=%s", slot_id)
            except Exception as exc:
                log.warning(
                    "rack_supervisor: stop error slot=%s exc=%s", slot_id, exc
                )

    def _deposit_descriptor(self) -> None:
        """Write the Ground Loop daemon YAML descriptor (atomic tmp→rename)."""
        self._plugin_dir.mkdir(parents=True, exist_ok=True)
        descriptor = {
            "name": "rack_supervisor",
            "mode": "daemon",
            "start_cmd": [sys.executable, "-m", "unseen_university.devices.claude.rack_supervisor"],
            "poll_interval": 30,
            "max_restarts": 10,
        }
        dest = self._plugin_dir / "rack_supervisor.yaml"
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(yaml.dump(descriptor, default_flow_style=False))
        tmp.rename(dest)
        log.info("rack_supervisor: deposited descriptor at %s", dest)


def _make_signal_handler(supervisor: RackSupervisor):
    def _handle(sig, _frame):
        log.info("rack_supervisor: signal=%s — stopping", sig)
        supervisor.stop()

    return _handle


def run(
    slots: list[str] | None = None,
    poll_interval: int = _DEFAULT_POLL,
    plugin_dir: Path = _PLUGIN_DIR,
) -> None:
    supervisor = RackSupervisor(
        slots=slots, poll_interval=poll_interval, plugin_dir=plugin_dir
    )
    handler = _make_signal_handler(supervisor)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    supervisor.start()
    supervisor.run_forever()


if __name__ == "__main__":
    _log_dir = _IGOR_HOME / "datacenter_logs" / "rack_supervisor" / "supervisor"
    _log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(_log_dir / "rack_supervisor.log"),
            logging.StreamHandler(),
        ],
    )
    run()
