"""
WorkspaceShim — lifecycle shim for the workspace rack device.

No external process to manage; shim satisfies the BaseShim contract.
Data classes here document the return shapes of WorkspaceDevice's MCP tools.
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.shim import BaseShim


@dataclass
class FileReadResult:
    status: str  # "ok" | "error"
    path: str = ""
    content: str = ""
    message: str = ""


@dataclass
class FileWriteResult:
    status: str  # "ok" | "error"
    path: str = ""
    message: str = ""


@dataclass
class BashResult:
    status: str  # "ok" | "error"
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    message: str = ""


class WorkspaceShim(BaseShim):
    """No external process — shim satisfies contract and does nothing."""

    @property
    def device_id(self) -> str:
        return "workspace"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "no external process"}

    def rollback(self) -> None:
        pass
