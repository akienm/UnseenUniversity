"""
health_monitor.py — Background source health pinger for the inference proxy.

Pings each registered Source on a configurable interval and updates
Source.available. The RulesEngine reads Source.available at routing time
so stale routes are skipped automatically.
"""

from __future__ import annotations

import logging
import threading
import time

from devices.inference.sources import SourceRegistry

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 60


class HealthMonitor:
    """Daemon thread that pings all sources and updates their availability."""

    def __init__(
        self,
        sources: SourceRegistry,
        interval_sec: int = _DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._sources = sources
        self._interval = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="inference-health", daemon=True
        )
        self._thread.start()
        log.info("HealthMonitor: started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("HealthMonitor: stopped")

    def check_now(self) -> dict[str, bool]:
        """Run one health check synchronously. Returns {source_name: available}."""
        results = {}
        for source in self._sources.all():
            try:
                available = source.check_and_update()
                source.available = (
                    available  # authoritative write — check_and_update may be mocked
                )
                results[source.name] = available
                if not available:
                    log.warning("HealthMonitor: source %r is DOWN", source.name)
            except Exception as exc:
                source.available = False
                results[source.name] = False
                log.warning("HealthMonitor: ping failed for %r — %s", source.name, exc)
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            self.check_now()
            self._stop.wait(timeout=self._interval)
