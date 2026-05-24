"""Health tools — rack_health() aggregated from heartbeat stream."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unseen_university.devices.librarian.health_aggregator import HealthAggregator

SCHEMAS = [
    {
        "name": "rack_health",
        "description": (
            "Return per-device health status collected from the heartbeat stream. "
            "Shows last-seen timestamp, age in seconds, uptime, and status "
            "(healthy / suspect / down). CC can call this instead of querying "
            "each device individually. Suspect: silent > 2× interval. "
            "Down: silent > 3× interval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# Module-level aggregator instance — set by Librarian on startup.
_aggregator: "HealthAggregator | None" = None


def set_aggregator(agg: "HealthAggregator") -> None:
    """Register the live HealthAggregator instance for rack_health calls."""
    global _aggregator
    _aggregator = agg


def rack_health() -> str:
    if _aggregator is None:
        return json.dumps({"error": "health aggregator not running", "devices": []})
    return json.dumps(_aggregator.rack_health(), default=str)


def dispatch(name: str, args: dict) -> str | None:
    if name == "rack_health":
        return rack_health()
    return None
