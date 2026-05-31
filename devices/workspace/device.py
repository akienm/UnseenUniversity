"""
WorkspaceDevice — rack device exposing read/write/bash as MCP tools.

Non-CC agents (Igor, Librarian, future workers) get filesystem access
through this device rather than calling OS primitives directly. Every
call is sandboxed: the resolved path must be under workspace_root.

MCP tools:
  workspace_read_file(path)                        → {ok, content}
  workspace_write_file(path, content)              → {ok}
  workspace_run_bash(command, timeout_sec)         → {ok, stdout, stderr, returncode}

workspace_root is injected at construction and defaults to WORKSPACE_ROOT
env var, falling back to CWD. Absolute path is stored; every tool call
resolves the input path and rejects anything that escapes the root.

D-agentic-os-platform-2026-05-30
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()

DEVICE_ID = "workspace"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceDevice(BaseDevice):
    """Exposes sandboxed read/write/bash tools to rack agents."""

    DEVICE_ID = DEVICE_ID

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        super().__init__()
        root = workspace_root or os.environ.get("WORKSPACE_ROOT") or os.getcwd()
        self._root = Path(root).resolve()
        self._errors: list[str] = []

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": DEVICE_ID,
            "name": "Workspace",
            "version": "0.1.0",
            "purpose": "sandboxed read/write/bash tools for rack agents",
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": [],
            "mcp_tools": [
                "workspace_read_file",
                "workspace_write_file",
                "workspace_run_bash",
            ],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{DEVICE_ID}",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        status = "healthy" if self._root.is_dir() else "degraded"
        return {
            "status": status,
            "detail": f"workspace_root={self._root}",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "in-process workspace device",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        self._errors.append("halted")

    def recovery(self) -> None:
        self._errors.clear()

    # ── Sandboxing ────────────────────────────────────────────────────────────

    def _resolve_safe(self, path: str) -> Path | None:
        """Resolve path and return it only when it is under workspace_root."""
        try:
            resolved = (self._root / path).resolve()
        except Exception:
            return None
        try:
            resolved.relative_to(self._root)
        except ValueError:
            return None
        return resolved

    # ── MCP tools ─────────────────────────────────────────────────────────────

    def workspace_read_file(self, path: str) -> dict[str, Any]:
        safe = self._resolve_safe(path)
        if safe is None:
            return {
                "status": "error",
                "path": path,
                "message": f"path {path!r} escapes workspace_root {str(self._root)!r}",
            }
        if not safe.exists():
            return {
                "status": "error",
                "path": path,
                "message": f"file not found: {path!r}",
            }
        if not safe.is_file():
            return {
                "status": "error",
                "path": path,
                "message": f"not a file: {path!r}",
            }
        try:
            content = safe.read_text(encoding="utf-8", errors="replace")
            return {"status": "ok", "path": path, "content": content}
        except OSError as exc:
            return {"status": "error", "path": path, "message": str(exc)}

    def workspace_write_file(self, path: str, content: str) -> dict[str, Any]:
        safe = self._resolve_safe(path)
        if safe is None:
            return {
                "status": "error",
                "path": path,
                "message": f"path {path!r} escapes workspace_root {str(self._root)!r}",
            }
        try:
            safe.parent.mkdir(parents=True, exist_ok=True)
            safe.write_text(content, encoding="utf-8")
            return {"status": "ok", "path": path}
        except OSError as exc:
            return {"status": "error", "path": path, "message": str(exc)}

    def workspace_run_bash(
        self, command: str, timeout_sec: float = 30.0
    ) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self._root),
                timeout=timeout_sec,
            )
            return {
                "status": "ok",
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"command timed out after {timeout_sec}s",
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
