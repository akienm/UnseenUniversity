"""
SensorDevice — system telemetry collection for the ADC rack.

Consolidates machine sensor readings that Igor previously collected inline
via ResourceMonitorSource in push_sources.py. Igor and other agents subscribe
via MCP instead of polling psutil directly.

Sensors collected:
  - cpu_percent         — 1-second sample, all cores
  - memory              — virtual memory % used + bytes available
  - disk                — per-mountpoint usage % and free bytes
  - temperatures        — per-sensor-chip readings (psutil.sensors_temperatures)
  - fans                — per-fan RPM (psutil.sensors_fans)
  - swap                — swap usage %
  - camera_devices      — stub: list of /dev/video* paths
  - audio_inputs        — stub: list of /dev/snd/pcm*c* capture devices

Exposes a read() method that returns a JSON-serializable dict.  The rack
MCP server calls read() to surface sensor data as an MCP tool.

SMART disk health is out of scope for this device — requires root and a
separate smartctl polling loop; file a follow-on ticket when needed.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from glob import glob
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_cpu() -> dict[str, Any]:
    import psutil

    per_cpu = psutil.cpu_percent(interval=1, percpu=True)
    return {
        "overall_percent": round(sum(per_cpu) / len(per_cpu), 1),
        "per_core": [round(v, 1) for v in per_cpu],
        "core_count": len(per_cpu),
    }


def _read_memory() -> dict[str, Any]:
    import psutil

    vm = psutil.virtual_memory()
    return {
        "percent_used": vm.percent,
        "available_mb": round(vm.available / 1024 / 1024, 1),
        "total_mb": round(vm.total / 1024 / 1024, 1),
    }


def _read_swap() -> dict[str, Any]:
    import psutil

    sw = psutil.swap_memory()
    return {
        "percent_used": sw.percent,
        "used_mb": round(sw.used / 1024 / 1024, 1),
        "total_mb": round(sw.total / 1024 / 1024, 1),
    }


def _read_disk() -> list[dict[str, Any]]:
    import psutil

    result = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            result.append(
                {
                    "mountpoint": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "percent_used": usage.percent,
                    "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
                    "total_gb": round(usage.total / 1024 / 1024 / 1024, 2),
                }
            )
        except PermissionError:
            pass
    return result


def _read_temperatures() -> dict[str, list[dict[str, Any]]]:
    try:
        import psutil

        raw = psutil.sensors_temperatures()
        if not raw:
            return {}
        result: dict[str, list] = {}
        for chip, entries in raw.items():
            result[chip] = [
                {
                    "label": e.label or chip,
                    "current_c": round(e.current, 1),
                    "high_c": round(e.high, 1) if e.high else None,
                    "critical_c": round(e.critical, 1) if e.critical else None,
                }
                for e in entries
            ]
        return result
    except (AttributeError, Exception):
        return {}


def _read_fans() -> dict[str, list[dict[str, Any]]]:
    try:
        import psutil

        raw = psutil.sensors_fans()
        if not raw:
            return {}
        result: dict[str, list] = {}
        for chip, entries in raw.items():
            result[chip] = [
                {"label": e.label or chip, "current_rpm": e.current} for e in entries
            ]
        return result
    except (AttributeError, Exception):
        return {}


def _discover_cameras() -> list[str]:
    return sorted(glob("/dev/video*"))


def _discover_audio_inputs() -> list[str]:
    return sorted(glob("/dev/snd/pcm*c*"))


class SensorDevice(BaseDevice):
    """
    In-process device that reads machine sensors via psutil.

    No subprocess — health() returns healthy as long as psutil reads succeed.
    """

    DEVICE_ID = "sensor"

    def __init__(self) -> None:
        super().__init__()
        self._startup_errors: list[str] = []
        try:
            import psutil as _psutil  # noqa: F401
        except ImportError as exc:
            self._startup_errors.append(f"psutil not available: {exc}")

    def read(self) -> dict[str, Any]:
        """Return all sensor data as a JSON-serializable dict."""
        return {
            "sampled_at": _now(),
            "cpu": _read_cpu(),
            "memory": _read_memory(),
            "swap": _read_swap(),
            "disk": _read_disk(),
            "temperatures": _read_temperatures(),
            "fans": _read_fans(),
            "cameras": _discover_cameras(),
            "audio_inputs": _discover_audio_inputs(),
        }

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "SensorDevice",
            "version": "0.1.0",
            "purpose": (
                "System telemetry: CPU, memory, disk, temps, fans, "
                "camera/mic discovery. Igor consumes via MCP."
            ),
        }

    def requirements(self) -> dict:
        return {"deps": ["psutil>=5.9"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": ["sensor_reading"],
            "mcp_endpoint": None,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/readings",
            "mode": "read_only",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._startup_errors:
            return {
                "status": "unhealthy",
                "detail": "; ".join(self._startup_errors),
                "checked_at": _now(),
            }
        try:
            import psutil

            psutil.cpu_percent(interval=0)
            psutil.virtual_memory()
            return {"status": "healthy", "detail": "psutil ok", "checked_at": _now()}
        except Exception as exc:
            return {
                "status": "degraded",
                "detail": f"psutil read failed: {exc}",
                "checked_at": _now(),
            }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "in-process — no subprocess",
        }

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        self._startup_errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._startup_errors.clear()
