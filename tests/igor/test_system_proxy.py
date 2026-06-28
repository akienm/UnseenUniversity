"""
test_system_proxy.py — T-system-proxy-facade

T-igor-network-remove: network/system_proxy.py removed. Skipped until
system_proxy relocates.

Tests for the SystemProxy facade. Uses real psutil where available,
mocks for edge cases.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

pass  # T-igor-channels-relocate: system_proxy moved to devices/igor/tools/system_proxy.py

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.tools.system_proxy import (
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


class TestHardwareProperty:
    def test_hardware_returns_dict(self):
        proxy = SystemProxy()
        hw = proxy.hardware
        assert isinstance(hw, dict)
        assert "hostname" in hw or hw == {}

    def test_hardware_cached_on_second_access(self):
        proxy = SystemProxy()
        hw1 = proxy.hardware
        hw2 = proxy.hardware
        assert hw1 is hw2

    def test_hardware_empty_on_import_failure(self):
        proxy = SystemProxy()
        with patch(
            "unseen_university.devices.igor.tools.hardware_detect.detect_hardware",
            side_effect=ImportError("nope"),
        ):
            if hasattr(proxy, "_hardware_cache"):
                del proxy._hardware_cache
            hw = proxy.hardware
        assert hw == {}


class TestNetworkProperty:
    def test_network_returns_proxy_or_none(self):
        proxy = SystemProxy()
        net = proxy.network
        if net is not None:
            assert hasattr(net, "get")
            assert hasattr(net, "post")
            assert hasattr(net, "report_str")

    def test_network_cached_on_second_access(self):
        proxy = SystemProxy()
        net1 = proxy.network
        net2 = proxy.network
        assert net1 is net2


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
