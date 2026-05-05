"""
AnnounceBroker — synchronous capability broker.

Takes an IdentityEnvelope, resolves the agent's profile, filters the
registry to allowed+online devices, and assembles a Manifest. The broker
itself is pure: no IMAP I/O, no clock state. The IMAP-driven dispatch
loop lives in announce.listener.AnnounceListener (slice 2), which calls
resolve_announce() on each inbound envelope.

Slice 1 (shipped): standalone in-process resolution.
Slice 2 (shipped): Skeleton registers the broker + an AnnounceListener
that pulls from comms://announce and publishes Manifests on
comms://announce-events.
Slice 3 (todo):    inotify-based push-on-change invalidation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .envelope import IdentityEnvelope
from .manifest import (
    ACL,
    MANIFEST_SCHEMA_VERSION,
    ChannelSubscription,
    Manifest,
    StateRef,
    ToolBinding,
    etag,
    profile_etag_from_yaml,
    registry_etag_from_dict,
)
from .profile import ProfileNotFoundError, load_profile, profile_yaml_etag

log = logging.getLogger(__name__)

_DEFAULT_INTERFACE = "imap_envelope"


class AnnounceError(Exception):
    """Raised when the broker cannot assemble a manifest."""


class AnnounceBroker:
    """
    Resolves an IdentityEnvelope to a Manifest.

    Args:
        profiles_dir: Directory containing <agent_id>.yaml profiles.
                      Defaults to ~/.agent_datacenter/profiles/ at runtime;
                      inject a temp dir in tests.
        registry:     DeviceRegistry (or dict-compatible snapshot).
                      list_devices() → list[dict] with keys: device_id, status.
        devices:      Mapping of device_id → live BaseDevice object.
                      Broker calls .comms() and .who_am_i() on each.
    """

    def __init__(
        self,
        profiles_dir: Path | str | None = None,
        registry=None,
        devices: dict | None = None,
    ) -> None:
        self._profiles_dir = Path(profiles_dir) if profiles_dir else None
        self._registry = registry
        self._devices: dict = devices or {}

    def resolve_announce(self, envelope: IdentityEnvelope) -> Manifest:
        """
        Assemble and return a Manifest for the agent described by envelope.

        Raises AnnounceError when the agent's profile is missing or the
        broker is in an inconsistent state.
        """
        try:
            profile = load_profile(envelope.agent_id, profiles_dir=self._profiles_dir)
        except ProfileNotFoundError as exc:
            raise AnnounceError(str(exc)) from exc

        p_etag = profile_yaml_etag(envelope.agent_id, profiles_dir=self._profiles_dir)

        online_devices = self._online_devices()
        r_etag = registry_etag_from_dict(
            {d["device_id"]: d.get("status") for d in online_devices}
        )

        assembler = ManifestAssembler(
            profile=profile,
            online_devices=online_devices,
            live_devices=self._devices,
        )

        tools = assembler.build_tool_bindings()
        subscriptions = assembler.build_channel_subscriptions()
        state_refs = assembler.build_state_refs()
        acl = assembler.build_acl()

        primary_addr = f"comms://{envelope.primary_mailbox}"
        surface_addresses = {
            surface: f"comms://{envelope.surface_mailbox(surface)}"
            for surface, active in profile.get("surfaces", {}).items()
            if active
        }

        return Manifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            issued_at=Manifest.now_iso(),
            issued_by=f"skeleton@{envelope.primary_mailbox}",
            issued_to={
                "agent_id": envelope.agent_id,
                "instance": envelope.instance,
                "box": envelope.box,
                "box_n": envelope.box_n,
            },
            manifest_id=Manifest.new_id(),
            tools=tools,
            subscriptions=subscriptions,
            state_refs=state_refs,
            acl=acl,
            surface_addresses=surface_addresses,
            primary_address=primary_addr,
            profile_version=profile.get("profile_version", "1.0"),
            profile_etag=p_etag,
            registry_etag=r_etag,
            visibility=profile.get("visibility", "secondary"),
        )

    def _online_devices(self) -> list[dict]:
        if self._registry is None:
            return []
        try:
            raw = self._registry.list_devices()
        except Exception as exc:
            log.warning("broker: could not list registry devices: %s", exc)
            return []
        # The flat-file DeviceRegistry exposes records keyed by 'id'; the
        # FakeRegistry used in slice-1 tests uses 'device_id'. Normalize so
        # downstream assembler code can rely on a consistent 'device_id' key.
        normalized: list[dict] = []
        for d in raw:
            if d.get("status") != "online":
                continue
            if "device_id" not in d and "id" in d:
                d = {**d, "device_id": d["id"]}
            normalized.append(d)
        return normalized


class ManifestAssembler:
    """
    Builds the sub-lists of a Manifest from a resolved profile + device snapshot.
    Pure: given same inputs produces same output.
    """

    def __init__(
        self,
        profile: dict,
        online_devices: list[dict],
        live_devices: dict,
    ) -> None:
        self._profile = profile
        self._online = {d["device_id"]: d for d in online_devices}
        self._live = live_devices

    def build_tool_bindings(self) -> list[ToolBinding]:
        allowed = set(self._profile.get("allowed_devices", []))
        perms = self._profile.get("device_permissions", {})
        bindings: list[ToolBinding] = []
        for device_id in allowed:
            if device_id not in self._online:
                log.debug(
                    "broker: device %r not online — excluded from manifest", device_id
                )
                continue
            device = self._live.get(device_id)
            address = self._device_address(device_id, device)
            mode = perms.get(device_id, {}).get("mode", "read_write")
            rate_limit = perms.get(device_id, {}).get("rate_limit_per_min")
            description = ""
            if device is not None:
                try:
                    description = device.who_am_i().get("name", device_id)
                except Exception:
                    pass
            bindings.append(
                ToolBinding(
                    name=device_id,
                    address=address,
                    interface=_DEFAULT_INTERFACE,
                    input_schema={},
                    output_schema=None,
                    permission_mode=mode,
                    rate_limit_per_min=rate_limit,
                    description=description,
                )
            )
        return bindings

    def build_channel_subscriptions(self) -> list[ChannelSubscription]:
        channels = list(self._profile.get("default_channels", []))
        if "shared" not in channels:
            channels.insert(0, "shared")
        return [
            ChannelSubscription(
                name=ch,
                address=f"comms://{ch}",
                role="member",
                notify_on_intent=True,
            )
            for ch in channels
        ]

    def build_state_refs(self) -> list[StateRef]:
        raw = self._profile.get("state_refs", {})
        if not isinstance(raw, dict):
            return []
        return [
            StateRef(name=name, uri=uri, mode="read_write") for name, uri in raw.items()
        ]

    def build_acl(self) -> ACL:
        raw = self._profile.get("acl", {})
        inbound = raw.get("inbound", {})
        outbound = raw.get("outbound", {})
        return ACL(
            inbound_allow=inbound.get("allow", []),
            inbound_deny=inbound.get("deny", []),
            outbound_allow=outbound.get("allow", []),
            outbound_deny=outbound.get("deny", []),
        )

    @staticmethod
    def _device_address(device_id: str, device) -> str:
        if device is not None:
            try:
                return device.comms()["address"]
            except Exception:
                pass
        return f"comms://{device_id}"
