"""
Envelope — the message shape for all bus traffic.

Design (Akien 2026-04-27): flex schema, not rigid 5-field lock. The rigid
keywords are well-known conventions that every device must include; the
payload is open so devices can extend without breaking the envelope contract.

Devices advertise what keywords they emit via their BaseDevice.capabilities()
method. The envelope does NOT validate payload shape — that's the device's job.

Mirrors Igor memory shape: {rigid-fields, payload: open dict}.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from dataclasses import asdict as _asdict
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"

# These keys must be present and non-empty in every envelope.
RIGID_KEYWORDS = frozenset({"from_device", "to_device", "sent_at", "schema_version"})

# Valid feed types (D-feeds-taxonomy-2026-06-11).
FEED_TYPES = frozenset({"public", "personal", "debug"})


@dataclass
class Envelope:
    from_device: str
    to_device: str
    sent_at: str  # ISO 8601 UTC
    schema_version: str = SCHEMA_VERSION
    payload: dict = field(default_factory=dict)
    # Feed type hint — informs PgBus routing and cap enforcement.
    # "personal": per-device feed, notification-eligible (default)
    # "public":   broadcast, no notifications
    # "debug":    console/log stream, capped at 1 000 messages, no notifications
    feed_type: str = "personal"
    # Importance level (0–10). NOTIFY is gated by mailbox notify_threshold.
    # 0=trace, 3=normal (default), 7=significant, 10=urgent
    importance: int = 3

    def validate(self) -> bool:
        """Return True if all rigid keywords are present and non-empty."""
        for key in RIGID_KEYWORDS:
            val = getattr(self, key, None)
            if not val:
                raise ValueError(f"Envelope missing required field: {key!r}")
        return True

    def to_json(self) -> str:
        return json.dumps(_asdict(self))

    @classmethod
    def from_json(cls, s: str) -> Envelope:
        data = json.loads(s)
        payload = data.pop("payload", {})
        return cls(**data, payload=payload)

    @classmethod
    def now(
        cls, from_device: str, to_device: str, payload: dict | None = None
    ) -> Envelope:
        """Convenience constructor with auto-filled sent_at."""
        return cls(
            from_device=from_device,
            to_device=to_device,
            sent_at=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        )
