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

from unseen_university._uu_root import uu_config_dir

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
from .provenance import ProvenanceService

log = logging.getLogger(__name__)

_DEFAULT_INTERFACE = "imap_envelope"

# Canonical profiles directory — same resolution as igor_shim._CANONICAL_PROFILES_DIR.
_CANONICAL_PROFILES_DIR = uu_config_dir() / "profiles"

# Fixed assembly order for well-known system prompt sections.
_PROMPT_SECTION_ORDER = ["rack", "recall", "channels", "agents", "questions"]


def _load_base_system_sections() -> dict:
    """Load system_prompt_sections from the canonical base.yaml.

    Returns an empty dict if base.yaml is absent or unreadable — callers
    degrade gracefully to an empty system_prompt rather than failing.
    """
    path = _CANONICAL_PROFILES_DIR / "base.yaml"
    if not path.exists():
        log.debug("broker: base.yaml not found at %s — no default system prompt", path)
        return {}
    try:
        import yaml  # type: ignore[import]

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return dict(data.get("system_prompt_sections", {}) or {})
    except Exception as exc:
        log.warning("broker: failed to load base.yaml system_prompt_sections: %s", exc)
    return {}


# Agent IDs whose announces require a non-empty proof field.
# These agents have known launch paths and can supply a shared secret.
# Extend via RACK_PROTECTED_AGENTS env var (comma-separated) at deploy time.
# Instance-based names (e.g. "igor-wild-0001") are protected by their base prefix.
import os as _os
_PROTECTED_AGENTS_DEFAULT = frozenset({"igor", "cc", "skeleton"})


def _protected_agents() -> frozenset[str]:
    env = _os.environ.get("RACK_PROTECTED_AGENTS", "")
    if env.strip():
        return frozenset(a.strip() for a in env.split(",") if a.strip())
    return _PROTECTED_AGENTS_DEFAULT


def _is_protected(agent_id: str) -> bool:
    """Return True if agent_id requires proof. Handles instance-based names."""
    return agent_id.split("-")[0] in _protected_agents()


class AnnounceError(Exception):
    """Raised when the broker cannot assemble a manifest."""


class AnnounceBroker:
    """
    Resolves an IdentityEnvelope to a Manifest.

    Args:
        profiles_dir: Directory containing <agent_id>.yaml profiles.
                      Defaults to ~/.unseen_university/profiles/ at runtime;
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
        provenance: ProvenanceService | None = None,
    ) -> None:
        self._profiles_dir = Path(profiles_dir) if profiles_dir else None
        self._registry = registry
        self._devices: dict = devices or {}
        # Security: ProvenanceService holds the HMAC rack secret at
        # ~/.unseen_university/rack.secret (permissions: 0o600, owner-only).
        # Agent worker processes (CC sessions, container shims) must NOT have
        # read access to this file. They receive issued tokens; they never hold
        # the signing key. See: T-sec-hmac-key-isolation.
        self._provenance = provenance
        self._base_system_sections: dict = _load_base_system_sections()

    def resolve_announce(self, envelope: IdentityEnvelope) -> Manifest:
        """
        Assemble and return a Manifest for the agent described by envelope.

        Raises AnnounceError when the agent's profile is missing or the
        broker is in an inconsistent state.
        """
        # T-announce-proof-validation: protected agent IDs must supply a non-empty proof.
        # This blocks identity impersonation (ContainerShim-tier announcing as 'igor').
        # Full challenge-response PKI is a follow-on; v1 just rejects empty proof.
        if _is_protected(envelope.agent_id) and not envelope.proof:
            raise AnnounceError(
                f"announce rejected: agent_id={envelope.agent_id!r} is protected — "
                f"a non-empty proof field is required (got empty proof)"
            )

        profile_agent_id = envelope.agent_id
        try:
            profile = load_profile(profile_agent_id, profiles_dir=self._profiles_dir)
        except ProfileNotFoundError:
            # Instance-based names (e.g. "igor-wild-0001") fall back to base profile.
            base = profile_agent_id.split("-")[0]
            if base == profile_agent_id:
                raise
            try:
                profile = load_profile(base, profiles_dir=self._profiles_dir)
                profile_agent_id = base
            except ProfileNotFoundError as exc:
                raise AnnounceError(str(exc)) from exc

        p_etag = profile_yaml_etag(profile_agent_id, profiles_dir=self._profiles_dir)

        online_devices = self._online_devices()
        r_etag = registry_etag_from_dict(
            {d["device_id"]: d.get("status") for d in online_devices}
        )

        assembler = ManifestAssembler(
            profile=profile,
            online_devices=online_devices,
            live_devices=self._devices,
            base_system_sections=self._base_system_sections,
        )

        tools = assembler.build_tool_bindings()
        subscriptions = assembler.build_channel_subscriptions()
        state_refs = assembler.build_state_refs()
        acl = assembler.build_acl()
        system_prompt = assembler.build_system_prompt()

        primary_addr = f"comms://{envelope.primary_mailbox}"
        surface_addresses = {
            surface: f"comms://{envelope.surface_mailbox(surface)}"
            for surface, active in profile.get("surfaces", {}).items()
            if active
        }

        token = None
        if self._provenance is not None:
            token = self._provenance.issue_token(
                envelope.agent_id, envelope.instance, envelope.ts
            )

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
            token=token,
            system_prompt=system_prompt,
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
        base_system_sections: dict | None = None,
    ) -> None:
        self._profile = profile
        self._online = {d["device_id"]: d for d in online_devices}
        self._live = live_devices
        self._base_sections: dict = base_system_sections or {}

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

    def build_system_prompt(self) -> str:
        """
        Assemble the manifest system_prompt from base sections + profile overrides.

        The profile's system_prompt_sections dict is merged over the base sections
        (profile values replace base values for matching keys; new keys are appended
        after the standard section order).  Returns an empty string when both base
        and profile have no sections.
        """
        profile_sections = self._profile.get("system_prompt_sections") or {}
        merged = {**self._base_sections, **profile_sections}
        if not merged:
            return ""
        parts: list[str] = []
        for key in _PROMPT_SECTION_ORDER:
            if key in merged:
                parts.append(str(merged[key]).rstrip())
        for key, value in merged.items():
            if key not in _PROMPT_SECTION_ORDER:
                parts.append(str(value).rstrip())
        return "\n\n".join(parts)

    @staticmethod
    def _device_address(device_id: str, device) -> str:
        if device is not None:
            try:
                return device.comms()["address"]
            except Exception:
                pass
        return f"comms://{device_id}"
