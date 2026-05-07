"""
hardware_detect.py — T-resource-auto-config (#445)

Auto-detect hardware specs on the current machine. Used by
machine_manager.register_self() to populate the machines table with
real values instead of "unknown".

Cross-platform: Linux, Windows, macOS. Falls back gracefully on any
detection failure.

Inertia: LOW (new tool)
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
from typing import Optional

from .registry import Tool, registry

logger = logging.getLogger(__name__)


def detect_hardware() -> dict:
    """Probe local hardware and return a dict of discovered specs.

    Keys: os, cpu, ram_gb, gpu, network_type, hostname, ip, ollama_model.
    Values are best-effort — any field can be "unknown" on failure.
    """
    hw = {
        "os": _detect_os(),
        "cpu": _detect_cpu(),
        "ram_gb": _detect_ram(),
        "gpu": _detect_gpu(),
        "network_type": _detect_network(),
        "hostname": socket.gethostname(),
        "ip": _detect_ip(),
        "ollama_model": _detect_ollama_model(),
    }
    return hw


def _detect_os() -> str:
    system = platform.system().lower()
    if system == "linux":
        try:
            import distro

            return f"linux ({distro.name()} {distro.version()})"
        except ImportError:
            return "linux"
    elif system == "windows":
        return f"windows ({platform.version()})"
    elif system == "darwin":
        return f"macos ({platform.mac_ver()[0]})"
    return system


def _detect_cpu() -> str:
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[-1].strip()
        elif platform.system() == "Windows":
            return platform.processor() or "unknown"
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    except Exception as e:
        logger.debug("_detect_cpu: subprocess failed: %s", e)
    return "unknown"


def _detect_ram() -> int:
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        return kb // (1024 * 1024)
        elif platform.system() == "Windows":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            c_ulong = ctypes.c_ulong

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", c_ulong),
                    ("dwMemoryLoad", c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return int(mem.ullTotalPhys / (1024**3))
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) // (1024**3)
    except Exception as e:
        logger.debug("_detect_ram: detection failed: %s", e)
    return 0


def _detect_gpu() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("_detect_gpu: nvidia-smi probe failed: %s", e)
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["lspci"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "VGA" in line or "3D" in line:
                        return line.split(":")[-1].strip()[:80]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("_detect_gpu: lspci probe failed: %s", e)
    return "none"


def _detect_network() -> str:
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["ip", "route", "get", "1.1.1.1"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                output = result.stdout
                if "wl" in output:
                    return "wifi"
                elif "eth" in output or "en" in output:
                    return "wired"
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("_detect_network: ifconfig probe failed: %s", e)
    return "unknown"


def _detect_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.0.0.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def _detect_ollama_model() -> str:
    try:
        import urllib.request
        import json

        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = data.get("models", [])
            if models:
                return models[0]["name"]
    except Exception as e:
        logger.debug("_detect_ollama_model: ollama probe failed: %s", e)
    return "unknown"


def detect_hardware_report(**_) -> str:
    """Detect and report local hardware specs."""
    hw = detect_hardware()
    lines = [f"Hardware detection for {hw['hostname']}:"]
    for key, val in hw.items():
        lines.append(f"  {key}: {val}")
    return "\n".join(lines)


registry.register(
    Tool(
        name="detect_hardware",
        description=(
            "Auto-detect hardware specs on this machine: CPU, RAM, GPU, "
            "OS, network type, Ollama model. Used for machine registration."
        ),
        parameters={"type": "object", "properties": {}},
        fn=detect_hardware_report,
    )
)
