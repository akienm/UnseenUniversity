"""
RestartGuard — window-based restart loop detection.

Tracks device restart failures in a sliding time window. When a device fails
N times within the window it is auto-blocked — the rack will not attempt
another restart until a human explicitly unblocks it.

Auto-block is distinct from manual block:
    manual  — operator issued block() or halt(); cleared by operator
    auto    — restart loop triggered; also cleared only by operator (agentctl unblock)

Both block types are surfaced in the registry record and in health() output.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from config.device_config import DeviceConfig

log = logging.getLogger(__name__)


class RestartGuard:
    def __init__(self) -> None:
        # device_id → list of failure timestamps (monotonic)
        self._failures: dict[str, list[float]] = defaultdict(list)

    def record_failure(self, device_id: str, config: DeviceConfig) -> None:
        """Record a restart failure and prune entries outside the window."""
        now = time.monotonic()
        self._failures[device_id].append(now)
        cutoff = now - config.restart_window_seconds
        self._failures[device_id] = [
            t for t in self._failures[device_id] if t >= cutoff
        ]

    def should_auto_block(self, device_id: str, config: DeviceConfig) -> bool:
        """Return True if failure count in window meets or exceeds the threshold."""
        now = time.monotonic()
        cutoff = now - config.restart_window_seconds
        in_window = [t for t in self._failures[device_id] if t >= cutoff]
        return len(in_window) >= config.max_restart_failures

    def auto_block(self, device_id: str, registry) -> None:
        """
        Mark device as auto-blocked in the registry.
        Registry record gains: status='blocked', block_type='auto', blocked_since=now.
        """
        data = registry._load()
        if device_id in data:
            data[device_id]["status"] = "blocked"
            data[device_id]["block_type"] = "auto"
            data[device_id]["blocked_since"] = datetime.now(timezone.utc).isoformat()
            registry._atomic_write(data)
            log.warning(
                "auto-blocked %s after restart loop (check logs and manually unblock)",
                device_id,
            )

    def clear(self, device_id: str) -> None:
        """Reset failure history for a device (called after successful restart)."""
        self._failures.pop(device_id, None)
