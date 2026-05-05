"""
Skeleton — MCP aggregator and central registry for agent_datacenter.

The skeleton is device #1 on every rack. It:
  - Exposes a single MCP endpoint (rack.*) for rack-level operations
  - Maintains the flat-file device registry (no Postgres dependency)
  - Detects namespace collisions at registration time (fails hard before start)
  - Proxies {device_id}.health to each registered device object
  - Creates IMAP mailboxes on device registration (mailboxes persist after deregistration)
  - Enforces v1 access control: halt/block require 'skeleton' or self as caller

v1 proxy scope: rack.* tools + per-device .health/.halt/.block tools.
Full tool-namespace proxying (transparent MCP-over-MCP) is a future ticket.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from agent_datacenter.announce import (
    ANNOUNCE_EVENTS_MAILBOX,
    ANNOUNCE_MAILBOX,
    AnnounceBroker,
    AnnounceListener,
)
from agent_datacenter.announce.channels import ChannelRegistry
from agent_datacenter.announce.manifest import INVALIDATE_MAILBOX
from agent_datacenter.device import BaseDevice, INTERFACE_VERSION
from agent_datacenter.skeleton.exceptions import AuthError, RegistrationError
from agent_datacenter.skeleton.health import (
    rack_channels,
    rack_devices,
    rack_health_async,
)
from config.device_config import DeviceConfig
from skeleton.registry import DeviceRegistry

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer
    from pathlib import Path

log = logging.getLogger(__name__)

_START_TIME = time.time()


class Skeleton(BaseDevice):
    DEVICE_ID = "skeleton"

    def __init__(
        self,
        registry: DeviceRegistry | None = None,
        imap_server: "IMAPServer | None" = None,
        profiles_dir: "Path | str | None" = None,
    ) -> None:
        self._registry = registry or DeviceRegistry()
        self._imap_server = imap_server
        self._devices: dict[str, BaseDevice] = {}  # live device objects
        self._announce_broker: AnnounceBroker | None = None
        self._announce_listener: AnnounceListener | None = None
        self._channel_registry: ChannelRegistry | None = None
        self._mcp = FastMCP("agent_datacenter")
        self._setup_rack_tools()
        # Register self in the flat-file registry
        self._registry.register(
            self.DEVICE_ID,
            DeviceConfig(manual_block_only=True),
            "comms://skeleton",
            name="Skeleton",
        )
        # Wire the announce protocol when a bus is attached.
        if self._imap_server is not None:
            self._bootstrap_announce(profiles_dir)
        log.info("skeleton initialized — rack.* tools registered")

    def _bootstrap_announce(self, profiles_dir) -> None:
        """
        Create the announce + announce-events mailboxes and wire the broker
        as a Skeleton sub-device. Pump is driven externally — a slice 3
        IDLE loop will replace the manual pump() call seen in tests.
        """
        for mailbox in (ANNOUNCE_MAILBOX, ANNOUNCE_EVENTS_MAILBOX, INVALIDATE_MAILBOX):
            try:
                self._imap_server.create_mailbox(mailbox)
            except Exception as exc:
                log.warning("announce: could not create mailbox %r: %s", mailbox, exc)
        try:
            self._imap_server.create_mailbox("shared")
        except Exception as exc:
            log.warning("announce: could not create shared channel mailbox: %s", exc)

        self._channel_registry = ChannelRegistry()
        self._announce_broker = AnnounceBroker(
            profiles_dir=profiles_dir,
            registry=self._registry,
            devices=self._devices,
        )
        self._announce_listener = AnnounceListener(
            broker=self._announce_broker,
            imap_server=self._imap_server,
            from_device=self.DEVICE_ID,
            channel_registry=self._channel_registry,
        )
        log.info(
            "announce: broker registered as skeleton sub-device "
            "(announce + announce-events mailboxes ready)"
        )

    def announce_pump(self) -> int:
        """Drive the announce listener once; returns processed envelope count."""
        if self._announce_listener is None:
            return 0
        return self._announce_listener.pump()

    @property
    def channels(self) -> ChannelRegistry | None:
        """The channel registry wired at announce time, or None if bus not attached."""
        return self._channel_registry

    # ── MCP tool registration ─────────────────────────────────────────────────

    def _setup_rack_tools(self) -> None:
        skel = self

        @self._mcp.tool()
        def rack_devices_tool() -> list[dict]:
            """List all registered devices and their current status."""
            return rack_devices(skel._registry)

        @self._mcp.tool()
        async def rack_health_tool() -> dict:
            """Return a parallel health rollup across all registered devices."""
            return await rack_health_async(skel._devices)

        @self._mcp.tool()
        def rack_channels_tool() -> list[str]:
            """List all IMAP mailbox names registered on this rack."""
            if skel._imap_server is None:
                return []
            return rack_channels(skel._imap_server)

    # ── Device registration ───────────────────────────────────────────────────

    def register_device(
        self,
        device: BaseDevice,
        config: DeviceConfig | None = None,
        mailbox: str | None = None,
    ) -> None:
        device_id = device.who_am_i()["device_id"]

        # Hard-fail on live collision; allow reattach when device is offline.
        # Offline reattach: device crashed or was deregistered but registry record persists.
        if device_id in self._devices:
            raise RegistrationError(
                f"Device '{device_id}' is already registered and online."
            )
        existing = self._registry.get_device(device_id)
        if existing and existing.get("status") != "offline":
            raise RegistrationError(
                f"Device '{device_id}' is already registered "
                f"(status='{existing['status']}'). "
                "Deregister or wait for offline status before re-registering."
            )

        cfg = config or DeviceConfig()
        mbox = mailbox or device.comms().get("address", f"comms://{device_id}/inbox")

        self._registry.register(
            device_id, cfg, mbox, name=device.who_am_i().get("name", device_id)
        )
        self._devices[device_id] = device

        # Create the device's IMAP mailbox. If it already exists (reattach after
        # offline), this is a no-op — IMAPServer.create_mailbox is idempotent.
        # Mailboxes are NOT deleted on deregistration; see deregister_device().
        if self._imap_server is not None:
            try:
                self._imap_server.create_mailbox(device_id)
            except Exception:
                log.warning(
                    "could not create mailbox for %s — messages will queue until available",
                    device_id,
                )

        # Expose {device_id}.health, .halt, .block as MCP tools
        self._add_device_health_tool(device_id, device)
        self._add_device_control_tools(device_id, device)
        log.info("registered device %s (mailbox=%s)", device_id, mbox)

    def deregister_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)
        self._registry.set_status(device_id, "offline")
        # Do NOT delete the IMAP mailbox. Messages are retained for 24hr (T-adc-imap-24hr-retention).
        # Manual cleanup is handled by agentctl cleanup-mailboxes (future).
        # Note: MCP tools registered via FastMCP are not dynamically removable in v1.
        # The tool remains but returns an error after deregistration.
        log.info(
            "deregistered device %s (mailbox retained for 24hr retention)", device_id
        )

    def _add_device_health_tool(self, device_id: str, device: BaseDevice) -> None:
        skel = self

        def make_health_tool(did: str, dev: BaseDevice):
            tool_name = f"{did}_health"

            @skel._mcp.tool(name=tool_name)
            def device_health() -> dict:
                f"""Return health for device '{did}'."""
                if did not in skel._devices:
                    return {
                        "status": "unhealthy",
                        "detail": f"device '{did}' deregistered",
                        "checked_at": _now(),
                    }
                try:
                    return dev.health()
                except Exception as e:
                    return {
                        "status": "unhealthy",
                        "detail": str(e),
                        "checked_at": _now(),
                    }

        make_health_tool(device_id, device)

    def _add_device_control_tools(self, device_id: str, device: BaseDevice) -> None:
        """
        Register {device_id}_halt and {device_id}_block MCP tools.

        v1 access control: halt and block require from_device == 'skeleton' or == device_id.
        Trust model is envelope-level (localhost trust); cryptographic ACL is Phase 5+.
        """
        skel = self

        def make_control_tools(did: str, dev: BaseDevice) -> None:
            @skel._mcp.tool(name=f"{did}_halt")
            def device_halt(from_device: str) -> dict:
                f"""Halt device '{did}'. Requires from_device == 'skeleton' or == '{did}'."""
                skel._check_caller_auth(from_device, did, "halt")
                if did not in skel._devices:
                    return {"error": f"device '{did}' not online"}
                dev.halt()
                return {"ok": True, "device_id": did, "op": "halt"}

            @skel._mcp.tool(name=f"{did}_block")
            def device_block(from_device: str, reason: str = "") -> dict:
                f"""Block device '{did}'. Requires from_device == 'skeleton' or == '{did}'."""
                skel._check_caller_auth(from_device, did, "block")
                if did not in skel._devices:
                    return {"error": f"device '{did}' not online"}
                dev.block(reason)
                skel._registry.set_status(did, "blocked")
                return {"ok": True, "device_id": did, "op": "block", "reason": reason}

        make_control_tools(device_id, device)

    def _check_caller_auth(
        self, from_device: str, target_device_id: str, op: str
    ) -> None:
        """Raise AuthError if from_device is not authorized to call op on target."""
        if from_device not in (self.DEVICE_ID, target_device_id):
            log.warning(
                "auth denied: from_device=%r attempted %s on %r",
                from_device,
                op,
                target_device_id,
            )
            raise AuthError(
                f"Unauthorized: {op} on '{target_device_id}' requires "
                f"from_device == 'skeleton' or == '{target_device_id}', "
                f"got '{from_device}'",
                from_device=from_device,
                target=target_device_id,
            )

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Skeleton",
            "version": "1.0.0",
            "purpose": "MCP aggregator and device registry",
        }

    def requirements(self) -> dict:
        return {"deps": ["mcp"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": [],
            "mcp_endpoint": "stdio",
        }

    def comms(self) -> dict:
        return {
            "address": "comms://skeleton",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        n = len(self._devices)
        return {
            "status": "healthy",
            "registered_devices": n,
            "detail": f"{n} device(s) on rack",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "agentctl init",
        }

    def restart(self) -> None:
        pass  # skeleton restart handled at process level

    def block(self, reason: str) -> None:
        log.warning("skeleton blocked: %s", reason)

    def halt(self) -> None:
        log.warning("skeleton halt requested")

    def recovery(self) -> None:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
