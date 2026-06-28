"""
cpu_peg_monitor.py — Scraps job: alert when CPU is pegged above threshold.

Samples cpu_percent() every SAMPLE_INTERVAL_SEC (default 5). When the rolling
window of consecutive samples stays above CPU_PEG_THRESHOLD (default 90%)
for at least CPU_PEG_SECONDS (default 30), posts once to the shared channel
with the top 3 CPU consumers. A cooldown prevents re-alerting until CPU drops
back below threshold for at least one sample.

Emits: CPU_PEG_ALERT|threshold=N|duration_sec=N|top=<proc list>
to the shared channel.

Env vars:
  CPU_PEG_THRESHOLD    int, percent (default 90)
  CPU_PEG_SECONDS      int, seconds (default 30)
  CPU_SAMPLE_INTERVAL  int, seconds between samples (default 5)

Run: python -m unseen_university.devices.scraps.jobs.cpu_peg_monitor
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Optional

log = logging.getLogger(__name__)

_THRESHOLD = int(os.environ.get("CPU_PEG_THRESHOLD", "90"))
_PEG_SECONDS = int(os.environ.get("CPU_PEG_SECONDS", "30"))
_SAMPLE_INTERVAL = int(os.environ.get("CPU_SAMPLE_INTERVAL", "5"))
_SAMPLES_NEEDED = max(1, _PEG_SECONDS // _SAMPLE_INTERVAL)


def _top_processes(n: int = 3) -> list[dict]:
    """Return top-N processes by CPU percent."""
    try:
        import psutil

        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                info = p.info
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
        return procs[:n]
    except Exception as exc:
        log.warning("cpu_peg_monitor: _top_processes failed: %s", exc)
        return []


def _sample_cpu() -> float:
    """Return system-wide CPU percent (non-blocking)."""
    try:
        import psutil

        return psutil.cpu_percent(interval=None)
    except Exception as exc:
        log.warning("cpu_peg_monitor: _sample_cpu failed: %s", exc)
        return 0.0


def _post_channel(msg: str) -> None:
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(msg, author="scraps", channel="shared")
    except Exception as exc:
        log.warning("cpu_peg_monitor: channel post failed: %s", exc)


class CpuPegMonitor:
    """Detects and alerts on sustained CPU pegging.

    Usage (long-running):
        monitor = CpuPegMonitor()
        monitor.run_forever()

    Usage (single-tick, for testing):
        monitor = CpuPegMonitor()
        monitor.tick(cpu_pct=95.0)
    """

    def __init__(
        self,
        threshold: int = _THRESHOLD,
        samples_needed: int = _SAMPLES_NEEDED,
        sample_interval: float = _SAMPLE_INTERVAL,
        _cpu_fn=None,
        _top_fn=None,
        _post_fn=None,
    ) -> None:
        self.threshold = threshold
        self.samples_needed = samples_needed
        self.sample_interval = sample_interval
        self._cpu_fn = _cpu_fn or _sample_cpu
        self._top_fn = _top_fn or _top_processes
        self._post_fn = _post_fn or _post_channel

        # Rolling window of booleans — True means that sample was above threshold
        self._window: deque[bool] = deque(maxlen=samples_needed)
        self._alerted: bool = False  # True while in an active peg event

    def _format_top(self, procs: list[dict]) -> str:
        parts = []
        for p in procs:
            name = p.get("name", "?")
            pid = p.get("pid", "?")
            cpu = p.get("cpu_percent") or 0
            parts.append(f"{name}({pid})={cpu:.0f}%")
        return ", ".join(parts) if parts else "n/a"

    def tick(self, cpu_pct: Optional[float] = None) -> bool:
        """Process one sample. Returns True if an alert was emitted this tick."""
        if cpu_pct is None:
            cpu_pct = self._cpu_fn()

        above = cpu_pct >= self.threshold
        self._window.append(above)

        if not above:
            # CPU dropped — clear alert latch so we can alert again next time
            if self._alerted:
                log.info(
                    "cpu_peg_monitor: CPU back below threshold (%.0f%% < %d%%)",
                    cpu_pct,
                    self.threshold,
                )
            self._alerted = False
            return False

        if self._alerted:
            # Already alerted for this peg event — don't spam
            return False

        if len(self._window) < self.samples_needed:
            # Window not full yet — not enough data
            return False

        if not all(self._window):
            # Some samples in window were below threshold — not a sustained peg
            return False

        # Sustained peg confirmed — emit alert
        top = self._top_fn()
        top_str = self._format_top(top)
        duration_sec = self.samples_needed * self.sample_interval
        msg = (
            f"CPU_PEG_ALERT|threshold={self.threshold}%"
            f"|duration_sec={duration_sec:.0f}"
            f"|cpu_pct={cpu_pct:.0f}"
            f"|top={top_str}"
        )
        log.warning("cpu_peg_monitor: %s", msg)
        self._post_fn(msg)
        self._alerted = True
        return True

    def run_forever(self) -> None:
        """Long-running poll loop. Blocks indefinitely."""
        log.info(
            "cpu_peg_monitor: starting — threshold=%d%% samples_needed=%d interval=%ds",
            self.threshold,
            self.samples_needed,
            int(self.sample_interval),
        )
        # Prime psutil's per-process cpu_percent (first call always returns 0)
        try:
            import psutil

            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        while True:
            self.tick()
            time.sleep(self.sample_interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    CpuPegMonitor().run_forever()
