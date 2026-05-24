"""
Invalidator — polling-based push of cache-invalidation signals.

Snapshots the SHA-256 etag of every YAML in the profiles_dir plus a
deterministic etag of the device registry. On each pump() the snapshot
is recomputed and diffed; any changed/added/removed profile, plus any
registry change, publishes a kind=invalidate envelope on
comms://announce-events.

Slice 3b ships the publish side. DatacenterClient consumers can poll
announce-events themselves and react (re-announce on match) — automatic
re-announce wiring is a separate ticket.

Polling rather than inotify keeps this portable across platforms; an
inotify backend can drop in later as a slice-3b-2 optimization. The
pump_once() seam means tests don't have to deal with timing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from bus.envelope import Envelope
from bus.imap_server import IMAPServer

from .manifest import (
    INVALIDATE_MAILBOX,
    profile_etag_from_yaml,
    registry_etag_from_dict,
)

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 2.0
REGISTRY_TARGET = "registry"


class Invalidator:
    """
    Background polling watcher over profiles_dir + (optional) registry.

    Args:
        profiles_dir: directory containing <agent_id>.yaml files.
        imap_server:  bus.IMAPServer used to publish invalidation envelopes.
        registry:     optional registry handle exposing list_devices(). When
                      provided, registry-level changes also trigger invalidates.
        from_device:  identifier on outbound envelopes (default 'invalidator').
    """

    def __init__(
        self,
        profiles_dir: Path | str,
        imap_server: IMAPServer,
        registry=None,
        from_device: str = "invalidator",
    ) -> None:
        self._profiles_dir = Path(profiles_dir)
        self._imap = imap_server
        self._registry = registry
        self._from_device = from_device
        self._profile_etags: dict[str, str] = self._snapshot_profiles()
        self._registry_etag: str = self._snapshot_registry()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Snapshot helpers ──────────────────────────────────────────────────────

    def _snapshot_profiles(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        if not self._profiles_dir.exists():
            return snapshot
        for path in sorted(self._profiles_dir.glob("*.yaml")):
            try:
                snapshot[path.stem] = profile_etag_from_yaml(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                log.warning(
                    "invalidator: could not read %s for snapshot: %s", path, exc
                )
        return snapshot

    def _snapshot_registry(self) -> str:
        if self._registry is None:
            return ""
        try:
            devices = self._registry.list_devices()
        except Exception as exc:
            log.warning("invalidator: registry list_devices failed: %s", exc)
            return self._registry_etag if hasattr(self, "_registry_etag") else ""
        snapshot = {d.get("device_id") or d.get("id"): d.get("status") for d in devices}
        return registry_etag_from_dict(snapshot)

    # ── Pump (testable seam) ─────────────────────────────────────────────────

    def pump_once(self) -> int:
        """
        Recompute snapshot, diff vs cached, publish invalidates for any
        changed/added/removed profile and for registry changes. Returns
        the number of invalidate envelopes published.
        """
        published = 0
        try:
            new_profiles = self._snapshot_profiles()
        except Exception as exc:
            log.warning("invalidator: pump snapshot failed: %s", exc)
            return 0

        old_keys = set(self._profile_etags)
        new_keys = set(new_profiles)
        for agent_id in sorted(new_keys - old_keys):
            self._publish_invalidate(agent_id, reason="added")
            published += 1
        for agent_id in sorted(old_keys - new_keys):
            self._publish_invalidate(agent_id, reason="removed")
            published += 1
        for agent_id in sorted(new_keys & old_keys):
            if new_profiles[agent_id] != self._profile_etags[agent_id]:
                self._publish_invalidate(agent_id, reason="changed")
                published += 1
        self._profile_etags = new_profiles

        new_registry = self._snapshot_registry()
        if new_registry != self._registry_etag:
            self._publish_invalidate(REGISTRY_TARGET, reason="changed")
            published += 1
            self._registry_etag = new_registry

        return published

    def _publish_invalidate(self, target: str, reason: str) -> None:
        env = Envelope.now(
            from_device=self._from_device,
            to_device=INVALIDATE_MAILBOX,
            payload={
                "kind": "invalidate",
                "target": target,  # agent_id or REGISTRY_TARGET
                "reason": reason,  # "added" | "removed" | "changed"
            },
        )
        try:
            self._imap.append(INVALIDATE_MAILBOX, env)
        except Exception as exc:
            log.warning(
                "invalidator: failed to publish invalidate for %r: %s", target, exc
            )

    # ── Background loop ───────────────────────────────────────────────────────

    def start(self, interval: float = DEFAULT_POLL_INTERVAL) -> None:
        """Spawn a daemon thread that calls pump_once() every interval seconds."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.pump_once()
                except Exception as exc:
                    log.warning("invalidator: pump_once failed (continuing): %s", exc)
                # Sleep in small chunks so stop() returns promptly.
                end = time.monotonic() + interval
                while time.monotonic() < end and not self._stop.is_set():
                    time.sleep(0.05)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Signal the background loop to exit and wait briefly for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
