"""
AuditorShim — lifecycle shim for the auditor rack device.

The auditor has no external process to manage; the shim is a no-op
lifecycle wrapper that satisfies the BaseShim contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unseen_university.shim import BaseShim


@dataclass
class AuditFinding:
    """A single audit check result."""

    name: str
    severity: str  # "high" | "med" | "low"
    status: str  # "PASS" | "FAIL" | "ERROR" | "ACKED"
    detail: str = ""


@dataclass
class AuditCheck:
    """A registered audit check definition."""

    name: str
    kind: str  # "shell" | "grep" | "sql" | "python"
    pattern: str
    severity: str
    description: str = ""
    added_by: str = ""
    mode: str = "forever"
    ack_until: str | None = None
    code: str = ""


class AuditorShim(BaseShim):
    """No external process — shim satisfies contract and does nothing."""

    @property
    def device_id(self) -> str:
        return "auditor"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        from devices.auditor.device import AuditorDevice, CHECKS_PATH

        if not CHECKS_PATH.exists():
            return {"passed": False, "details": f"checks file not found: {CHECKS_PATH}"}
        device = AuditorDevice()
        h = device.health()
        return {"passed": h["status"] == "healthy", "details": h["detail"]}

    def rollback(self) -> None:
        pass
