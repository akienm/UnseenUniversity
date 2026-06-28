"""
test_hardware_detect.py — T-resource-auto-config (#445)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.tools.hardware_detect import (  # noqa: E402
    _detect_cpu,
    _detect_ip,
    _detect_network,
    _detect_os,
    _detect_ram,
    detect_hardware,
    detect_hardware_report,
)


class TestDetectHardware:
    def test_returns_dict(self):
        hw = detect_hardware()
        assert isinstance(hw, dict)
        assert "os" in hw
        assert "cpu" in hw
        assert "ram_gb" in hw
        assert "hostname" in hw

    def test_os_not_empty(self):
        assert _detect_os()
        assert len(_detect_os()) > 0

    def test_cpu_returns_string(self):
        result = _detect_cpu()
        assert isinstance(result, str)

    def test_ram_returns_int(self):
        result = _detect_ram()
        assert isinstance(result, int)
        assert result >= 0

    def test_ip_returns_string(self):
        result = _detect_ip()
        assert isinstance(result, str)

    def test_network_returns_string(self):
        result = _detect_network()
        assert isinstance(result, str)

    def test_report_contains_hostname(self):
        report = detect_hardware_report()
        assert "hostname" in report

    def test_tool_registered(self):
        from unseen_university.devices.igor.tools.registry import registry

        t = registry.get("detect_hardware")
        assert t is not None
