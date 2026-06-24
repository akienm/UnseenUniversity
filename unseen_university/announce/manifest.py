"""
Manifest — the dynamic capability snapshot the datacenter assembles for each
agent that announces itself.

Issued by AnnounceBroker in response to an IdentityEnvelope. Agents cache
this and use it to build their tool dispatch table, system-prompt capability
layer, and channel subscription list.

Cache key: profile_etag + registry_etag.  When either changes, the broker
pushes an invalidate event to comms://announce-events and agents re-fetch.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

MANIFEST_SCHEMA_VERSION = "1.0"
ANNOUNCE_EVENTS_MAILBOX = "announce-events"
# Cache-invalidation pushes go to a separate mailbox so consumers reading
# invalidates (DatacenterClient.check_for_invalidate) don't race with the
# poll loop in DatacenterClient.announce(). § 14 / slice 3b architecture.
INVALIDATE_MAILBOX = "invalidate"


@dataclass
class ToolBinding:
    name: str  # MCP-style, e.g. "inference.complete"
    address: str  # comms:// routing address
    interface: str  # "mcp" | "imap_envelope" | "http" | "python_callable"
    input_schema: dict
    output_schema: dict | None
    permission_mode: str  # "read_only" | "write_only" | "read_write"
    rate_limit_per_min: int | None = None
    description: str = ""


@dataclass
class ChannelSubscription:
    name: str  # e.g. "shared", "igor-cc"
    address: str  # e.g. "comms://shared"
    role: str  # "member" | "observer"
    notify_on_intent: bool = True


@dataclass
class StateRef:
    name: str  # "twm", "ne", "milieu"
    # Relative "#fragment" (e.g. "#twm") resolved against UU_HOME_DB_URL at connect
    # time via identity.compose_state_uri(); or a full URI ("file://..."). Carries no
    # credential — this rides the announce manifest across the bus, so a baked
    # postgres URL would persist the live password in mailboxes.
    uri: str
    mode: str  # "read_only" | "read_write"


@dataclass
class ACL:
    inbound_allow: list[str] = field(default_factory=list)
    inbound_deny: list[str] = field(default_factory=list)
    outbound_allow: list[str] = field(default_factory=list)
    outbound_deny: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    schema_version: str
    issued_at: str  # ISO 8601 UTC
    issued_by: str  # "skeleton@akiendelllinux.1"
    issued_to: dict  # echo of IdentityEnvelope (agent_id, instance, box, box_n)
    manifest_id: str  # uuid4

    tools: list[ToolBinding]
    subscriptions: list[ChannelSubscription]
    state_refs: list[StateRef]
    acl: ACL

    surface_addresses: dict  # {"console": "comms://akiendelllinux.1.console", ...}
    primary_address: str  # "comms://akiendelllinux.1"

    profile_version: str
    profile_etag: str  # SHA-256 of the profile YAML
    registry_etag: str  # SHA-256 of the relevant registry slice

    expires_at: str | None = None
    visibility: str = "secondary"  # "primary" | "secondary" — tab ordering hint
    token: str | None = (
        None  # rack-issued provenance token (None when service not wired)
    )
    system_prompt: str = ""  # rack orientation text injected at announce time

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def new_id(cls) -> str:
        return str(uuid.uuid4())

    @classmethod
    def now_iso(cls) -> str:
        return datetime.now(timezone.utc).isoformat()


def etag(data: bytes | str) -> str:
    """SHA-256 hex digest of bytes or UTF-8 string — used for profile/registry etags."""
    b = data if isinstance(data, bytes) else data.encode()
    return hashlib.sha256(b).hexdigest()


def profile_etag_from_yaml(yaml_text: str) -> str:
    return etag(yaml_text)


def registry_etag_from_dict(registry_snapshot: dict) -> str:
    return etag(json.dumps(registry_snapshot, sort_keys=True))
