"""
system_proxy.py — T-system-proxy-facade

Single facade for all local system metrics: CPU, memory, disk, GPU,
process stats. Cached readings with configurable staleness. Thread-safe.

All scattered psutil calls across push_sources, inference_gateway,
filesystem, training_corpus should route through here.

Usage:
    from wild_igor.igor.network.system_proxy import system_proxy
    snap = system_proxy.snapshot()
    cpu = system_proxy.cpu_percent()
    mem = system_proxy.memory()

Inertia: LOW (new file)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..igor_base import IgorBase

logger = logging.getLogger(__name__)

_DEFAULT_STALENESS_SEC = 2.0


@dataclass
class MemoryInfo:
    total_gb: float
    available_gb: float
    percent: float
    swap_total_gb: float = 0.0
    swap_used_gb: float = 0.0
    swap_percent: float = 0.0


@dataclass
class DiskInfo:
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


@dataclass
class ProcessInfo:
    pid: int
    rss_mb: float
    vms_mb: float
    cpu_percent: float
    threads: int


@dataclass
class SystemSnapshot:
    """Point-in-time snapshot of all system metrics."""

    cpu_percent: float = 0.0
    memory: Optional[MemoryInfo] = None
    disk: Optional[DiskInfo] = None
    process: Optional[ProcessInfo] = None
    timestamp: float = field(default_factory=time.monotonic)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "cpu_percent": self.cpu_percent,
            "timestamp": self.timestamp,
        }
        if self.memory:
            d["memory"] = {
                "total_gb": self.memory.total_gb,
                "available_gb": self.memory.available_gb,
                "percent": self.memory.percent,
                "swap_percent": self.memory.swap_percent,
            }
        if self.disk:
            d["disk"] = {
                "total_gb": self.disk.total_gb,
                "free_gb": self.disk.free_gb,
                "percent": self.disk.percent,
            }
        if self.process:
            d["process"] = {
                "pid": self.process.pid,
                "rss_mb": self.process.rss_mb,
                "threads": self.process.threads,
            }
        if self.error:
            d["error"] = self.error
        return d


class SystemProxy(IgorBase):
    """Single object for all local system metrics.

    Thread-safe, cached. Callers get fresh data if cache is stale,
    cached data otherwise.
    """

    def __init__(self, staleness_sec: float = _DEFAULT_STALENESS_SEC) -> None:
        self._staleness_sec = staleness_sec
        self._lock = threading.Lock()
        self._cached_snapshot: Optional[SystemSnapshot] = None
        self._cached_at: float = 0.0

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._cached_at) > self._staleness_sec

    def _read_cpu(self) -> float:
        try:
            import psutil

            return psutil.cpu_percent(interval=None)
        except Exception:
            return 0.0

    def _read_memory(self) -> Optional[MemoryInfo]:
        try:
            import psutil

            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            return MemoryInfo(
                total_gb=round(vm.total / (1024**3), 2),
                available_gb=round(vm.available / (1024**3), 2),
                percent=vm.percent,
                swap_total_gb=round(sw.total / (1024**3), 2),
                swap_used_gb=round(sw.used / (1024**3), 2),
                swap_percent=sw.percent,
            )
        except Exception:
            return None

    def _read_disk(self, path: str = "/") -> Optional[DiskInfo]:
        try:
            import psutil

            du = psutil.disk_usage(path)
            return DiskInfo(
                total_gb=round(du.total / (1024**3), 2),
                used_gb=round(du.used / (1024**3), 2),
                free_gb=round(du.free / (1024**3), 2),
                percent=du.percent,
            )
        except Exception:
            return None

    def _read_process(self) -> Optional[ProcessInfo]:
        try:
            import psutil

            proc = psutil.Process(os.getpid())
            mi = proc.memory_info()
            return ProcessInfo(
                pid=proc.pid,
                rss_mb=round(mi.rss / (1024**2), 1),
                vms_mb=round(mi.vms / (1024**2), 1),
                cpu_percent=proc.cpu_percent(),
                threads=proc.num_threads(),
            )
        except Exception:
            return None

    def _refresh(self) -> SystemSnapshot:
        try:
            snap = SystemSnapshot(
                cpu_percent=self._read_cpu(),
                memory=self._read_memory(),
                disk=self._read_disk(),
                process=self._read_process(),
            )
        except Exception as e:
            snap = SystemSnapshot(error=str(e))
        self._cached_snapshot = snap
        self._cached_at = time.monotonic()
        return snap

    def snapshot(self) -> SystemSnapshot:
        """Get a full system snapshot. Returns cached if fresh."""
        with self._lock:
            if self._is_stale() or self._cached_snapshot is None:
                return self._refresh()
            return self._cached_snapshot

    def cpu_percent(self) -> float:
        return self.snapshot().cpu_percent

    def memory(self) -> Optional[MemoryInfo]:
        return self.snapshot().memory

    def disk(self, path: str = "/") -> Optional[DiskInfo]:
        snap = self.snapshot()
        if path != "/":
            return self._read_disk(path)
        return snap.disk

    def process(self) -> Optional[ProcessInfo]:
        return self.snapshot().process

    def is_under_pressure(
        self, cpu_threshold: float = 90.0, mem_threshold: float = 90.0
    ) -> bool:
        """Quick check: is the system under resource pressure?"""
        snap = self.snapshot()
        if snap.cpu_percent >= cpu_threshold:
            return True
        if snap.memory and snap.memory.percent >= mem_threshold:
            return True
        return False

    @property
    def hardware(self) -> dict[str, Any]:
        """Static hardware inventory — probed once at first access."""
        if not hasattr(self, "_hardware_cache"):
            try:
                from ..tools.hardware_detect import detect_hardware

                self._hardware_cache: dict[str, Any] = detect_hardware()
            except Exception:
                self._hardware_cache = {}
        return self._hardware_cache

    def report_str(self) -> str:
        """Human-readable one-liner for dashboards and audits."""
        snap = self.snapshot()
        parts = [f"CPU: {snap.cpu_percent:.0f}%"]
        if snap.memory:
            parts.append(
                f"MEM: {snap.memory.percent:.0f}% ({snap.memory.available_gb:.1f}GB free)"
            )
        if snap.disk:
            parts.append(
                f"DISK: {snap.disk.percent:.0f}% ({snap.disk.free_gb:.0f}GB free)"
            )
        if snap.process:
            parts.append(f"PID: {snap.process.pid} RSS: {snap.process.rss_mb:.0f}MB")
        return " | ".join(parts)


# Module-level singleton
system_proxy = SystemProxy()
