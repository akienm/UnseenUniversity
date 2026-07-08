"""Proof-on-close for T-collapse-daemons-to-ground-loop.

Two load-bearing behaviors the collapse must preserve — each red on a hollow build:

1. **Logs from the shim loop thread land in the canonical per-device JSON sink.**
   ``DiagnosticBase.__init__`` installs the sink + stdlib intercept but does NOT stamp
   ``device_id`` process-wide (that was ``configure_process_logging``, now retired). The
   sink DROPS any record without ``device_id``. So the loop thread MUST wrap its body in
   ``logger.contextualize(device_id=...)`` or every dispatch/health record silently
   vanishes — the exact stale-log failure this lineage keeps hitting. Without the
   contextualize wrap in ``ShimLoopThread._run``, no file appears → this test goes red.

2. **The dispatch-health WARN still fires from the driven (shim-owned) path**, not the
   deleted standalone ``run_loop``. If the health emit isn't wired into the loop's
   ``on_cycle``, it never runs → this test goes red.
"""

from __future__ import annotations

import json
import logging
import threading
import time


def test_loop_thread_logs_land_with_device_id(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_LOG_ROOT", str(tmp_path))

    # ORDERING-PROOF: reset loguru's process-global default extra to empty. Otherwise a
    # prior test that called configure_process_logging("granny") would leave device_id
    # stamped process-wide, and this proof would pass EVEN WITH the contextualize wrap
    # deleted (false green). After this reset, device_id can ONLY reach the record via
    # ShimLoopThread._run's logger.contextualize — so a hollow build (no wrap) goes red.
    # Safe: the device's own logs use logger.bind(...), independent of the global default.
    from loguru import logger as _loguru

    _loguru.configure(extra={})

    # Install the JSON sink + stdlib intercept, as booting the device does. The device
    # does NOT stamp device_id process-wide — that's exactly why the loop must contextualize.
    from unseen_university.devices.granny.device import GrannyWeatherwaxDevice

    GrannyWeatherwaxDevice()

    from unseen_university.shim import ShimLoopThread

    emitted = threading.Event()

    def tick():
        logging.getLogger("unseen_university.devices.granny.daemon").info(
            "collapse-proof probe record"
        )
        emitted.set()

    loop = ShimLoopThread("granny", tick, interval=0.01)
    loop.start()
    assert emitted.wait(2.0), "tick should run"
    # Poll briefly for the JSON file (sink writes synchronously, but be tolerant).
    info_dir = tmp_path / "granny" / "info"
    records = []
    for _ in range(200):
        if info_dir.exists():
            records = [json.loads(f.read_text()) for f in info_dir.glob("*.json")]
            if any("collapse-proof probe" in r.get("message", "") for r in records):
                break
        time.sleep(0.01)
    loop.stop()

    matched = [r for r in records if "collapse-proof probe" in r.get("message", "")]
    assert matched, (
        "no granny JSON log record landed — the loop thread's stdlib logs were dropped "
        "(missing logger.contextualize(device_id=...) inside the thread body)"
    )
    assert matched[0]["device_id"] == "granny"


def test_dispatch_health_fires_from_driven_path(monkeypatch):
    import unseen_university.devices.granny.daemon as gd
    from unseen_university.devices.granny.shim import GrannyShim

    calls = []
    fired = threading.Event()
    monkeypatch.setattr(gd, "_make_imap_if_bus_configured", lambda cfg: None)
    monkeypatch.setattr(gd, "run_once", lambda cfg, imap=None: None)
    monkeypatch.setattr(gd, "_load_config", lambda: {})
    monkeypatch.setattr(
        gd, "_emit_dispatch_health", lambda cfg: (calls.append(1), fired.set())
    )
    monkeypatch.setattr(gd, "_HEALTH_EVERY_N", 1)  # emit every cycle

    shim = GrannyShim()
    shim._start_dispatch_loop()
    try:
        assert fired.wait(2.0), (
            "dispatch-health emit never fired — it isn't wired into the shim-driven loop"
        )
    finally:
        shim.stop()
    assert calls, "expected the dispatch-health emit to run from the driven path"
