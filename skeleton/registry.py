"""
DeviceRegistry — flat-file device registry for the skeleton.

Why flat-file and not Postgres: the skeleton manages Postgres. If the registry
lived in Postgres, skeleton couldn't restart Postgres when Postgres goes down —
circular dependency. A JSON flat file breaks the cycle.

File location: {runtime_dir}/devices.json (default ~/.unseen_university/devices.json)
Write discipline: always write to .tmp then rename — never corrupts main file on crash.

Device record shape:
    {
        "id": str,
        "name": str,
        "status": "online" | "offline" | "blocked",
        "mailbox": str,          # comms:// URI
        "config": {...},         # DeviceConfig.to_dict()
        "registered_at": str,    # ISO 8601
    }

Address resolution (T-swarm-identity-layer):
    resolve() accepts two forms:
      comms://CC.0                    — local alias; looked up by mailbox
      akiendell.cc.0/console          — box-qualified global form; resolves to
                                        the same device when box == local hostname.
                                        Cross-box addresses (box != hostname) return None;
                                        the bus routing layer handles cross-box delivery.

    Agent-type → local mailbox mapping (box-qualified → comms://):
      cc.<n>        → comms://CC.<n>
      igor.<n>      → comms://igor-wild-<n:04d>   (e.g. igor.0 → igor-wild-0001)
      skeleton.<n>  → comms://skeleton
      <other>.<n>   → comms://<other>.<n>          (passthrough)

    Surface suffixes (/console, /mcp, /inference) are preserved in the returned
    record as a "surface" key and do not affect device lookup.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

from config.device_config import DeviceConfig

log = logging.getLogger(__name__)

DEFAULT_REGISTRY_DIR = Path.home() / ".unseen_university"
DEFAULT_REGISTRY_PATH = DEFAULT_REGISTRY_DIR / "devices.json"


class DeviceRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._atomic_write({})

    def register(
        self, device_id: str, config: DeviceConfig, mailbox: str, name: str = ""
    ) -> None:
        data = self._load()
        data[device_id] = {
            "id": device_id,
            "name": name or device_id,
            "status": "online",
            "mailbox": mailbox,
            "config": config.to_dict(),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._atomic_write(data)
        log.info("registered device %s at %s", device_id, mailbox)

    def deregister(self, device_id: str) -> None:
        data = self._load()
        if device_id in data:
            del data[device_id]
            self._atomic_write(data)
            log.info("deregistered device %s", device_id)

    def list_devices(self) -> list[dict]:
        return list(self._load().values())

    def get_device(self, device_id: str) -> dict | None:
        return self._load().get(device_id)

    def set_status(self, device_id: str, status: str) -> None:
        data = self._load()
        if device_id in data:
            data[device_id]["status"] = status
            self._atomic_write(data)

    def ping_on_restart(self, ping_fn) -> None:
        """
        Called after skeleton restart. For each registered device, call ping_fn(device_id)
        which returns True if reachable. Sets status online/offline accordingly.
        ping_fn signature: (device_id: str) -> bool
        """
        data = self._load()
        changed = False
        for device_id, record in data.items():
            if record.get("status") == "blocked":
                continue
            reachable = False
            try:
                reachable = ping_fn(device_id)
            except Exception:
                pass
            new_status = "online" if reachable else "offline"
            if record["status"] != new_status:
                record["status"] = new_status
                changed = True
                log.info("device %s → %s (ping-on-restart)", device_id, new_status)
        if changed:
            self._atomic_write(data)

    # ── Address resolution (T-swarm-identity-layer) ───────────────────────────

    def resolve(self, address: str) -> dict | None:
        """
        Resolve a comms:// or <box>.<agent_type>.<n>[/surface] address.

        comms:// form: looked up directly by mailbox field.
        box-qualified form: parsed, validated against local hostname, then
            mapped to a comms:// mailbox and looked up.

        Returns a device record dict (with optional "surface" key added for
        surface-qualified addresses), or None if not found / cross-box.
        """
        if address.startswith("comms://"):
            return self._find_by_mailbox(address)

        # box.agent_type.n[/surface] form
        path, surface = _split_surface(address)
        parts = path.split(".")
        if len(parts) != 3:
            return None
        box, agent_type, n_str = parts

        if box != _local_hostname():
            return None  # cross-box: not locally resolvable

        try:
            n = int(n_str)
        except ValueError:
            return None

        mailbox = _agent_mailbox(agent_type.lower(), n)
        record = self._find_by_mailbox(mailbox)
        if record is not None and surface is not None:
            record = {**record, "surface": surface}
        return record

    def _find_by_mailbox(self, mailbox: str) -> dict | None:
        """Return the first device record whose mailbox matches, or None."""
        for record in self._load().values():
            if record.get("mailbox") == mailbox:
                return dict(record)
        return None

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
            log.warning("registry file corrupt or missing — starting empty")
            return {}


# ── Module-level helpers for address resolution ───────────────────────────────


def _local_hostname() -> str:
    return socket.gethostname()


def _split_surface(address: str) -> tuple[str, str | None]:
    """Split 'box.agent.n/surface' into ('box.agent.n', 'surface') or ('path', None)."""
    if "/" in address:
        path, surface = address.split("/", 1)
        return path, surface or None
    return address, None


def _agent_mailbox(agent_type: str, n: int) -> str:
    """
    Map (agent_type, instance_n) to the local comms:// mailbox address.

    Known mappings:
      cc     → comms://CC.<n>          (e.g. cc.0 → comms://CC.0)
      igor   → comms://igor-wild-<n:04d>  (e.g. igor.0 → comms://igor-wild-0001)
               Note: igor instance numbering is 1-based (first = wild-0001).
      skeleton → comms://skeleton       (singleton; n ignored)
      other  → comms://<agent_type>.<n>  (passthrough for devices like inference.0)
    """
    if agent_type == "cc":
        return f"comms://CC.{n}"
    if agent_type == "igor":
        return f"comms://igor-wild-{n + 1:04d}"
    if agent_type == "skeleton":
        return "comms://skeleton"
    return f"comms://{agent_type}.{n}"
