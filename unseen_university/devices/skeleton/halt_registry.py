"""
HaltRegistry — flat-file agent halt state for the kill switch.

Keyed by agent_id (igor, cc, etc.) — same namespace as config/policies/*.yaml
and ~/.unseen_university/registry/tokens.json. NOT keyed by device_id.

File: ~/.unseen_university/registry/halted.json
Write discipline: write to .tmp then rename (crash-safe).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_HALT_REGISTRY_PATH = (
    Path.home() / ".unseen_university" / "registry" / "halted.json"
)


class HaltRegistry:
    """
    Flat-file store for agent halt state. Survives rack restarts.

    agent_id namespace matches config/policies/{agent_id}.yaml — not device_id.
    """

    def __init__(self, path: Path = DEFAULT_HALT_REGISTRY_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._atomic_write({})

    def set_halted(self, agent_id: str, halted: bool, reason: str = "") -> None:
        """Set or clear halt state for agent_id. Idempotent."""
        data = self._load()
        if halted:
            data[agent_id] = {
                "halted": True,
                "halt_reason": reason,
                "halted_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            data.pop(agent_id, None)
        self._atomic_write(data)
        log.info(
            "agent %s %s (reason=%r)",
            agent_id,
            "halted" if halted else "resumed",
            reason,
        )

    def is_halted(self, agent_id: str) -> tuple[bool, str]:
        """Return (is_halted, reason). Defaults to (False, '') for unknown agents."""
        record = self._load().get(agent_id, {})
        return record.get("halted", False), record.get("halt_reason", "")

    def _atomic_write(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._path)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning(
                "halt registry corrupt or missing — treating all agents as not halted"
            )
            return {}
