"""
test_system_proxy.py — T-system-proxy-facade

Tests for the SystemProxy facade. Uses real psutil where available,
mocks for edge cases.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.network.system_proxy import (
    DiskInfo,
    MemoryInfo,
    ProcessInfo,
    SystemProxy,
    SystemSnapshot,
    system_proxy,
)


class TestSystemSnapshot:
    def test_to_dict_basic(self):
        snap = SystemSnapshot(cpu_percent=42.0)
        d = snap.to_dict()
        assert d["cpu_percent"] == 42.0
        assert "timestamp" in d

    def test_to_dict_with_memory(self):
        snap = SystemSnapshot(
            cpu_percent=10.0,
            memory=MemoryInfo(
                total_gb=32.0,
                available_gb=16.0,
                percent=50.0,
            ),
        )
        d = snap.to_dict()
        assert d["memory"]["total_gb"] == 32.0
        assert d["memory"]["percent"] == 50.0

    def test_to_dict_with_error(self):
        snap = SystemSnapshot(error="psutil not available")
        d = snap.to_dict()
        assert d["error"] == "psutil not available"


class TestSystemProxy:
    def test_snapshot_returns_system_snapshot(self):
        proxy = SystemProxy()
        snap = proxy.snapshot()
        assert isinstance(snap, SystemSnapshot)
        assert snap.cpu_percent >= 0.0

    def test_cpu_percent_returns_float(self):
        proxy = SystemProxy()
        cpu = proxy.cpu_percent()
        assert isinstance(cpu, float)

    def test_memory_returns_memory_info(self):
        proxy = SystemProxy()
        mem = proxy.memory()
        if mem is not None:
            assert isinstance(mem, MemoryInfo)
            assert mem.total_gb > 0

    def test_disk_returns_disk_info(self):
        proxy = SystemProxy()
        disk = proxy.disk()
        if disk is not None:
            assert isinstance(disk, DiskInfo)
            assert disk.total_gb > 0

    def test_process_returns_process_info(self):
        proxy = SystemProxy()
        proc = proxy.process()
        if proc is not None:
            assert isinstance(proc, ProcessInfo)
            assert proc.pid > 0

    def test_caching_returns_same_snapshot(self):
        proxy = SystemProxy(staleness_sec=10.0)
        snap1 = proxy.snapshot()
        snap2 = proxy.snapshot()
        assert snap1 is snap2

    def test_stale_cache_refreshes(self):
        proxy = SystemProxy(staleness_sec=0.0)
        snap1 = proxy.snapshot()
        snap2 = proxy.snapshot()
        assert snap1 is not snap2

    def test_is_under_pressure_false_normally(self):
        proxy = SystemProxy()
        assert isinstance(proxy.is_under_pressure(), bool)

    def test_report_str_returns_string(self):
        proxy = SystemProxy()
        report = proxy.report_str()
        assert isinstance(report, str)
        assert "CPU:" in report


class TestModuleSingleton:
    def test_singleton_exists(self):
        assert system_proxy is not None
        assert isinstance(system_proxy, SystemProxy)


class TestPsutilUnavailable:
    def test_cpu_returns_zero_when_psutil_missing(self):
        proxy = SystemProxy(staleness_sec=0.0)
        with patch.dict("sys.modules", {"psutil": None}):
            with patch("builtins.__import__", side_effect=ImportError("no psutil")):
                snap = proxy._refresh()
        assert snap.cpu_percent == 0.0

    def test_memory_returns_none_when_psutil_missing(self):
        proxy = SystemProxy()
        with patch.dict("sys.modules", {"psutil": None}):
            with patch("builtins.__import__", side_effect=ImportError("no psutil")):
                result = proxy._read_memory()
        assert result is None
