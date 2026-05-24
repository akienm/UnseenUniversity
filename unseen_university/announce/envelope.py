"""
IdentityEnvelope — the payload an agent sends to announce itself.

Sent as the payload of a bus Envelope to comms://announce when an agent
boots or re-announces. The broker extracts the IdentityEnvelope from
the bus Envelope's payload, resolves the agent's profile, and assembles
a Manifest in response.

Wire shape is a plain dict (flex-schema per bus design). Required fields
raise ValidationError on from_dict(); extra keys are preserved.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field

ANNOUNCE_SCHEMA_VERSION = "1.0"

# Flat mailbox name for the announce broker inbox.
# Bus address: comms://announce  (suffix-style, § 14 decision 11.A)
ANNOUNCE_MAILBOX = "announce"


class ValidationError(ValueError):
    """Raised when an IdentityEnvelope is missing required fields."""


@dataclass
class IdentityEnvelope:
    """
    Identity envelope sent by an agent at plug-in time.

    Mandatory: agent_id, instance, box, box_n, pid, interface_version.
    Optional:  lineage (metadata only — NOT used for routing per E-decision),
               coa_id, surfaces, declared_capabilities, proof.
    """

    # Mandatory
    agent_id: str  # logical type, e.g. "igor", "cc", "research-orca"
    instance: str  # this-process identifier, e.g. "wild-0001"
    box: str  # hostname, e.g. "akiendelllinux"
    box_n: int  # instance number on this box (0-indexed)
    pid: int  # OS pid for liveness debugging
    interface_version: str  # matches BaseDevice.INTERFACE_VERSION

    announce_schema: str = ANNOUNCE_SCHEMA_VERSION

    # Optional
    lineage: str = ""  # metadata only — not for routing
    coa_id: str = ""
    surfaces: list[str] = field(default_factory=list)  # ["console", "mcp", "inference"]
    declared_capabilities: list[str] = field(default_factory=list)
    proof: dict = field(default_factory=dict)  # §3.3 — locality trust for v1
    ts: float = field(default_factory=time.time)

    # ── Derived address helpers ───────────────────────────────────────────────

    @property
    def primary_mailbox(self) -> str:
        """Flat mailbox name for this agent's primary inbox, e.g. 'akiendelllinux.0'."""
        return f"{self.box}.{self.box_n}"

    def surface_mailbox(self, surface: str) -> str:
        """Suffix-style surface mailbox, e.g. 'akiendelllinux.0.console'."""
        return f"{self.primary_mailbox}.{surface}"

    def coa_mailbox(self, coa_id: str | None = None) -> str:
        """COA mailbox, e.g. 'akiendelllinux.0.coa-2'."""
        c = coa_id or self.coa_id
        if not c:
            raise ValueError("coa_id required for coa_mailbox")
        return f"{self.primary_mailbox}.{c}"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IdentityEnvelope":
        required = {"agent_id", "instance", "box", "box_n", "pid", "interface_version"}
        missing = required - d.keys()
        if missing:
            raise ValidationError(
                f"IdentityEnvelope missing required fields: {missing}"
            )
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    # ── Convenience factory ───────────────────────────────────────────────────

    @classmethod
    def for_this_process(
        cls,
        agent_id: str,
        instance: str,
        interface_version: str,
        box_n: int = 0,
        **kwargs,
    ) -> "IdentityEnvelope":
        """Build an envelope for the current running process."""
        import socket

        return cls(
            agent_id=agent_id,
            instance=instance,
            box=socket.gethostname(),
            box_n=box_n,
            pid=os.getpid(),
            interface_version=interface_version,
            **kwargs,
        )
