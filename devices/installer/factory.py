"""
FactoryInstantiator — load a factory YAML spec, validate it, wire member manifests,
and report health/eval rollup to the factory owner.

Instantiation flow:
  1. load_spec(path)      — parse YAML, validate required fields
  2. instantiate(spec)    — build MemberManifests with factory channel wired in;
                            post FACTORY_ANNOUNCE to owner_id
  3. health_rollup(inst)  — collect member health, post FACTORY_HEALTH to owner_id
  4. report_eval(inst, …) — post FACTORY_EVAL scores to owner_id
  5. escalate(inst, …)    — post FACTORY_ESCALATE to owner_id (orchestrator blocked)
  6. halt(inst)           — set status=halted, post FACTORY_HALT to owner_id

Design rules:
  - owner_id is always a comms:// address. Treated uniformly — humans and agents
    are both just comms:// endpoints. Terminal-human is where the chain ends, not
    a special branch in this code.
  - Manifest wiring uses unseen_university.announce.manifest types so the factory
    record is compatible with the announce protocol. The AnnounceBroker is NOT
    called here; manifests are assembled directly from profiles. Live agents
    reconcile on their next announce cycle.
  - channel_post_fn is injectable so tests capture posts without a live DB.
"""

from __future__ import annotations

import importlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from unseen_university.announce.manifest import (
    ACL,
    MANIFEST_SCHEMA_VERSION,
    ChannelSubscription,
    Manifest,
    StateRef,
    ToolBinding,
    etag,
    profile_etag_from_yaml,
)

log = logging.getLogger(__name__)

_REQUIRED_SPEC_FIELDS = ("factory_id", "owner_id", "orchestrator", "members")
_VALID_STATUSES = frozenset({"pending", "approved", "active", "halted", "archived"})
_VALID_ORCHESTRATORS = frozenset({"granny-pattern"})
_PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "profiles"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Data types ──────────────────────────────────────────────────────────────────


@dataclass
class FactorySpec:
    factory_id: str
    factory_version: str
    description: str
    status: str
    owner_id: str
    orchestrator: str
    members: list[dict]
    eval_rubric: str | None
    budget_limits: dict
    escalation: dict


@dataclass
class FactoryInstance:
    spec: FactorySpec
    member_manifests: list[Manifest]
    orchestrator_address: str
    status: str  # "active" | "halted"
    instantiated_at: str


# ── Spec loading ────────────────────────────────────────────────────────────────


def load_spec(path: Path) -> FactorySpec:
    """Parse and validate a factory YAML spec.

    Raises ValueError listing all errors if the spec is invalid.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: YAML root must be a mapping")

    errors: list[str] = []
    for f in _REQUIRED_SPEC_FIELDS:
        if not raw.get(f):
            errors.append(f"missing required field: {f}")

    if not errors:
        status = raw.get("status", "pending")
        if status not in _VALID_STATUSES:
            errors.append(
                f"invalid status: {status!r} (must be one of {sorted(_VALID_STATUSES)})"
            )

        orch = raw.get("orchestrator", "")
        if orch not in _VALID_ORCHESTRATORS:
            errors.append(
                f"unknown orchestrator: {orch!r} (known: {sorted(_VALID_ORCHESTRATORS)})"
            )

        owner_id = raw.get("owner_id", "")
        if not str(owner_id).startswith("comms://"):
            errors.append(f"owner_id must be a comms:// address, got: {owner_id!r}")

        members = raw.get("members", [])
        if not isinstance(members, list) or len(members) < 1:
            errors.append(
                "members must be a non-empty list of {agent_type: ...} entries"
            )
        else:
            for i, m in enumerate(members):
                if not isinstance(m, dict) or "agent_type" not in m:
                    errors.append(f"members[{i}] must have an 'agent_type' key")

    if errors:
        joined = "; ".join(errors)
        raise ValueError(
            f"factory spec {path.name} has {len(errors)} error(s): {joined}"
        )

    return FactorySpec(
        factory_id=raw["factory_id"],
        factory_version=raw.get("factory_version", "1.0"),
        description=raw.get("description", ""),
        status=raw.get("status", "pending"),
        owner_id=raw["owner_id"],
        orchestrator=raw["orchestrator"],
        members=list(raw["members"]),
        eval_rubric=raw.get("eval_rubric"),
        budget_limits=raw.get("budget_limits") or {},
        escalation=raw.get("escalation") or {},
    )


# ── Manifest assembly ───────────────────────────────────────────────────────────


def _load_profile_yaml(agent_type: str, profiles_dir: Path) -> tuple[str, dict]:
    """Load profile YAML for agent_type. Returns (yaml_text, parsed_dict).

    Returns ("", {}) when the profile file is not found.
    """
    python_name = agent_type.replace("-", "_")
    for name in (agent_type, python_name):
        path = profiles_dir / f"{name}.yaml"
        if path.exists():
            text = path.read_text()
            return text, yaml.safe_load(text)
    log.warning(
        "factory: no profile found for agent_type=%r in %s", agent_type, profiles_dir
    )
    return "", {}


def _build_member_manifest(
    agent_type: str,
    factory_id: str,
    orchestrator_address: str,
    profiles_dir: Path,
) -> Manifest:
    """Assemble a Manifest for a factory member, with the factory orchestrator channel wired in.

    Uses the agent's config/profiles/<agent_type>.yaml as the capability source.
    Profile absence is non-fatal: the manifest is built with empty tools/channels
    (except the factory orchestrator channel, which is always added).
    """
    yaml_text, profile = _load_profile_yaml(agent_type, profiles_dir)

    python_name = agent_type.replace("-", "_")

    # Channels: profile defaults + factory orchestrator channel (the "wiring")
    default_channels: list[str] = profile.get("default_channels", ["shared"])
    subscriptions: list[ChannelSubscription] = [
        ChannelSubscription(
            name=ch,
            address=f"comms://{ch}",
            role="member",
            notify_on_intent=True,
        )
        for ch in default_channels
    ]
    subscriptions.append(
        ChannelSubscription(
            name=f"{factory_id}/orchestrator",
            address=orchestrator_address,
            role="member",
            notify_on_intent=True,
        )
    )

    # Tools: one ToolBinding per allowed device
    allowed_devices: list[str] = profile.get("allowed_devices", [])
    device_perms: dict = profile.get("device_permissions", {})
    tools: list[ToolBinding] = [
        ToolBinding(
            name=device,
            address=f"comms://{device}",
            interface="imap_envelope",
            input_schema={},
            output_schema=None,
            permission_mode=device_perms.get(device, {}).get("mode", "read_write"),
        )
        for device in allowed_devices
    ]

    # ACL
    acl_raw: dict = profile.get("acl", {})
    acl = ACL(
        inbound_allow=acl_raw.get("inbound", {}).get("allow", ["*"]),
        inbound_deny=acl_raw.get("inbound", {}).get("deny", []),
        outbound_allow=acl_raw.get("outbound", {}).get("allow", ["*"]),
        outbound_deny=acl_raw.get("outbound", {}).get("deny", []),
    )

    # State refs
    state_refs_raw: dict = profile.get("state_refs", {})
    state_refs: list[StateRef] = [
        StateRef(name=k, uri=v, mode="read_write") for k, v in state_refs_raw.items()
    ]

    # Surface addresses
    surfaces: dict = profile.get("surfaces", {})
    surface_addresses = {
        surf: f"comms://{python_name}.factory.{surf}"
        for surf, enabled in surfaces.items()
        if enabled
    }

    p_etag = (
        profile_etag_from_yaml(yaml_text)
        if yaml_text
        else etag(f"no-profile:{agent_type}")
    )
    r_etag = etag(f"factory:{factory_id}")

    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        issued_at=_now_iso(),
        issued_by=f"factory-instantiator/{factory_id}",
        issued_to={"agent_id": agent_type, "factory_id": factory_id},
        manifest_id=str(uuid.uuid4()),
        tools=tools,
        subscriptions=subscriptions,
        state_refs=state_refs,
        acl=acl,
        surface_addresses=surface_addresses,
        primary_address=f"comms://{python_name}.factory",
        profile_version=profile.get("profile_version", "1.0"),
        profile_etag=p_etag,
        registry_etag=r_etag,
        visibility=profile.get("visibility", "secondary"),
    )


# ── FactoryInstantiator ─────────────────────────────────────────────────────────


def _address_to_channel(owner_id: str) -> str:
    """Derive a channel name from a comms:// address.

    comms://akien/  → "shared"   (human owner posts to shared channel)
    comms://granny  → "granny"   (agent owner posts to their channel)
    """
    if owner_id.startswith("comms://"):
        host = owner_id[len("comms://") :].split("/")[0].rstrip(".")
        # Humans post to "shared" — agent names map to their own channel.
        # Heuristic: treat bare names with no dots as human aliases.
        return "shared" if not host or host == "akien" else host
    return owner_id


class FactoryInstantiator:
    """Factory lifecycle controller.

    All reporting (health, eval, escalation) goes to owner_id via channel post.
    channel_post_fn is injectable so tests can capture posts without a live DB.
    """

    def __init__(
        self,
        profiles_dir: Path = _PROFILES_DIR,
        channel_post_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self._profiles_dir = profiles_dir
        self._post = channel_post_fn or _default_post

    def instantiate(self, spec: FactorySpec) -> FactoryInstance:
        """Wire member manifests and post FACTORY_ANNOUNCE to owner.

        Does not spawn processes — wiring is manifest-level. Live agents
        reconcile by re-announcing through the broker on their next cycle.
        """
        orchestrator_address = f"comms://{spec.factory_id}/orchestrator"

        member_manifests = [
            _build_member_manifest(
                m["agent_type"],
                spec.factory_id,
                orchestrator_address,
                self._profiles_dir,
            )
            for m in spec.members
        ]

        instance = FactoryInstance(
            spec=spec,
            member_manifests=member_manifests,
            orchestrator_address=orchestrator_address,
            status="active",
            instantiated_at=_now_iso(),
        )

        member_types = ",".join(m["agent_type"] for m in spec.members)
        self._post(
            spec.owner_id,
            (
                f"FACTORY_ANNOUNCE"
                f"|factory={spec.factory_id}"
                f"|members={member_types}"
                f"|orchestrator={spec.orchestrator}"
                f"|status=active"
            ),
        )
        return instance

    def health_rollup(self, instance: FactoryInstance) -> dict:
        """Collect member health and report to owner_id.

        Returns a health dict: {factory_id, overall, members, checked_at}.
        """
        import json

        member_health: dict[str, str] = {}
        for m in instance.member_manifests:
            agent_type = m.issued_to["agent_id"]
            member_health[agent_type] = _probe_member_health(agent_type)

        overall = (
            "healthy"
            if all(h in ("healthy", "unknown") for h in member_health.values())
            else "degraded"
        )
        rollup = {
            "factory_id": instance.spec.factory_id,
            "overall": overall,
            "members": member_health,
            "checked_at": _now_iso(),
        }
        self._post(
            instance.spec.owner_id,
            (
                f"FACTORY_HEALTH"
                f"|factory={instance.spec.factory_id}"
                f"|overall={overall}"
                f"|detail={json.dumps(member_health)}"
            ),
        )
        return rollup

    def report_eval(self, instance: FactoryInstance, eval_scores: dict) -> None:
        """Report eval scores to owner_id."""
        import json

        self._post(
            instance.spec.owner_id,
            (
                f"FACTORY_EVAL"
                f"|factory={instance.spec.factory_id}"
                f"|rubric={instance.spec.eval_rubric}"
                f"|scores={json.dumps(eval_scores)}"
            ),
        )

    def escalate(self, instance: FactoryInstance, reason: str) -> None:
        """Post escalation to owner when the orchestrator is blocked.

        Same pattern as GrannyWeatherwaxDevice.escalate_to_cc.
        """
        self._post(
            instance.spec.owner_id,
            f"FACTORY_ESCALATE|factory={instance.spec.factory_id}|reason={reason}",
        )

    def halt(self, instance: FactoryInstance) -> None:
        """Halt the factory: set status=halted, notify owner.

        Seam for T-agent-kill-switch — that ticket wires the kill mechanism;
        this method provides the status transition.
        """
        instance.status = "halted"
        self._post(
            instance.spec.owner_id,
            f"FACTORY_HALT|factory={instance.spec.factory_id}",
        )


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _probe_member_health(agent_type: str) -> str:
    """Try to call health() on the member device module.

    Returns "healthy" | "degraded" | "unknown".
    "unknown" when the device module is not importable (not yet scaffolded).
    """
    python_name = agent_type.replace("-", "_")
    try:
        mod = importlib.import_module(f"devices.{python_name}.device")
        for attr in dir(mod):
            cls = getattr(mod, attr)
            if (
                isinstance(cls, type)
                and hasattr(cls, "health")
                and hasattr(cls, "DEVICE_ID")
                and cls.__name__ != "BaseDevice"
            ):
                result = cls().health()
                return result.get("status", "unknown")
    except Exception as exc:
        log.debug("health probe for %r failed: %s", agent_type, exc)
    return "unknown"


def _default_post(owner_id: str, message: str) -> None:
    """Post to shared channel (or owner-specific channel for agent owners).

    Gracefully no-ops when no channel is configured — never blocks the caller.
    """
    channel = _address_to_channel(owner_id)
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(message, author="factory-instantiator", channel=channel)
    except Exception as exc:
        log.warning("factory channel post failed (%s): %s", channel, exc)
