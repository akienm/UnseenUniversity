"""HealthAggregator — collects heartbeats, tracks per-device status.

Sits in IMAP IDLE on the heartbeat mailbox. For each heartbeat received,
updates the last-seen table. Silence thresholds:

    > 2 * interval_s  → suspect
    > 3 * interval_s  → down

Warm-up clause: silence checks are suppressed for the first 2 * interval_s
seconds after aggregator startup, allowing the table to repopulate on restart.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer

log = logging.getLogger(__name__)

_HEARTBEAT_MAILBOX = "heartbeat"
_IDLE_KEEPALIVE_S: float = 25 * 60

# Status literals
_HEALTHY = "healthy"
_SUSPECT = "suspect"
_DOWN = "down"
_UNKNOWN = "unknown"


class DeviceStatus:
    __slots__ = (
        "device_id",
        "last_ts",
        "uptime_s",
        "health",
        "current_action",
        "status",
    )

    def __init__(
        self,
        device_id: str,
        last_ts: datetime,
        uptime_s: float,
        health: str,
        current_action: str,
    ) -> None:
        self.device_id = device_id
        self.last_ts = last_ts
        self.uptime_s = uptime_s
        self.health = health
        self.current_action = current_action
        self.status = health  # updated by silence check

    def age_s(self) -> float:
        return (datetime.now(timezone.utc) - self.last_ts).total_seconds()

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "last_seen": self.last_ts.isoformat(),
            "age_s": round(self.age_s(), 1),
            "uptime_s": self.uptime_s,
            "health": self.health,
            "current_action": self.current_action,
            "status": self.status,
        }


class HealthAggregator:
    """Collects heartbeat envelopes from the IMAP bus; exposes rack_health().

    Args:
        imap_server:  IMAPServer to read heartbeats from.
        interval_s:   Expected heartbeat interval in seconds. Silence thresholds
                      are multiples of this value. Should match the senders'
                      start_heartbeat(interval_s=…) value. Default: 30.
    """

    def __init__(self, imap_server: "IMAPServer", interval_s: float = 30.0) -> None:
        self._imap = imap_server
        self._interval_s = interval_s
        self._table: dict[str, DeviceStatus] = {}
        self._lock = threading.Lock()
        self._started_at = datetime.now(timezone.utc)

    # ── Public API ────────────────────────────────────────────────────────────

    def pump(self) -> int:
        """Fetch and process all unseen heartbeat envelopes. Returns count."""
        try:
            envelopes = self._imap.fetch_unseen(_HEARTBEAT_MAILBOX)
        except Exception as exc:
            log.warning("health-aggregator: fetch failed: %s", exc)
            return 0
        for env in envelopes:
            self._ingest(env)
        return len(envelopes)

    def rack_health(self) -> dict:
        """Return per-device status dict, applying silence thresholds."""
        self._apply_silence_checks()
        with self._lock:
            return {
                "devices": [s.to_dict() for s in self._table.values()],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    # Check stop every _STOP_POLL_S seconds so the loop exits promptly when
    # stop.set() is called rather than blocking for the full IDLE timeout.
    _STOP_POLL_S: float = 0.25

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Run IDLE loop until stop is set."""
        log.info("health-aggregator: entering IDLE loop")
        while stop is None or not stop.is_set():
            try:
                woke = self._imap.idle_wait(
                    _HEARTBEAT_MAILBOX, timeout_s=self._STOP_POLL_S
                )
                if woke:
                    self.pump()
            except Exception as exc:
                log.warning("health-aggregator: IDLE loop error: %s", exc)
        log.info("health-aggregator: IDLE loop stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ingest(self, env) -> None:
        payload = env.payload if hasattr(env, "payload") else {}
        device_id = payload.get("device_id")
        if not device_id:
            log.debug("health-aggregator: heartbeat missing device_id, skipping")
            return

        ts_str = payload.get("ts", "")
        try:
            last_ts = datetime.fromisoformat(ts_str)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            last_ts = datetime.now(timezone.utc)

        with self._lock:
            self._table[device_id] = DeviceStatus(
                device_id=device_id,
                last_ts=last_ts,
                uptime_s=float(payload.get("uptime_s", 0.0)),
                health=str(payload.get("health", _UNKNOWN)),
                current_action=str(payload.get("current_action", "")),
            )
        log.debug("health-aggregator: heartbeat from %s", device_id)

    def _warm_up_active(self) -> bool:
        """Returns True if we're still within the warm-up window."""
        age = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        return age < 2 * self._interval_s

    def _apply_silence_checks(self) -> None:
        if self._warm_up_active():
            return
        suspect_threshold = 2 * self._interval_s
        down_threshold = 3 * self._interval_s
        with self._lock:
            for s in self._table.values():
                age = s.age_s()
                if age > down_threshold:
                    if s.status != _DOWN:
                        log.warning(
                            "health-aggregator: %s silent for %.0fs — marking DOWN",
                            s.device_id,
                            age,
                        )
                    s.status = _DOWN
                elif age > suspect_threshold:
                    if s.status not in (_SUSPECT, _DOWN):
                        log.warning(
                            "health-aggregator: %s silent for %.0fs — marking SUSPECT",
                            s.device_id,
                            age,
                        )
                    s.status = _SUSPECT
                else:
                    s.status = s.health
