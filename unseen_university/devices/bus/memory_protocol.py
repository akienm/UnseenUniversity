"""
bus/memory_protocol.py — Typed schemas for inter-agent memory access requests.

When an agent needs access to another agent's tier-specific memories, it sends
a MemoryAccessRequest envelope via the bus. The owning agent responds with a
MemoryAccessResponse. Neither party accesses the other's DB directly.

See C-clan-instance-scoping for the full boundary rules and protocol spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MemoryAccessRequest:
    """Bus payload for requesting access to another agent's tier memories.

    kind: "memory.access_request" — used by owning agent to dispatch.
    from_agent: requesting agent instance ID (e.g. "librarian-wild-0001")
    to_agent: owning agent instance ID (e.g. "igor-wild-0001")
    scope: "agent" | "client" — which tier is being requested
    query: natural-language or structured query string
    intent: why the requesting agent needs this (logged; used for audit)
    max_results: optional cap on returned memories (default 10)
    """

    kind: str = "memory.access_request"
    from_agent: str = ""
    to_agent: str = ""
    scope: str = "agent"
    query: str = ""
    intent: str = ""
    max_results: int = 10

    def to_payload(self) -> dict:
        return {
            "kind": self.kind,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "scope": self.scope,
            "query": self.query,
            "intent": self.intent,
            "max_results": self.max_results,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "MemoryAccessRequest":
        return cls(
            kind=payload.get("kind", "memory.access_request"),
            from_agent=payload.get("from_agent", ""),
            to_agent=payload.get("to_agent", ""),
            scope=payload.get("scope", "agent"),
            query=payload.get("query", ""),
            intent=payload.get("intent", ""),
            max_results=int(payload.get("max_results", 10)),
        )


@dataclass
class MemoryRecord:
    """A single memory returned in a MemoryAccessResponse."""

    id: str = ""
    narrative: str = ""
    memory_type: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "narrative": self.narrative,
            "memory_type": self.memory_type,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        return cls(
            id=d.get("id", ""),
            narrative=d.get("narrative", ""),
            memory_type=d.get("memory_type", ""),
            metadata=d.get("metadata", {}),
        )


@dataclass
class MemoryAccessResponse:
    """Bus payload returned by the owning agent in response to MemoryAccessRequest.

    kind: "memory.access_response"
    request_id: echoes the request envelope UUID for correlation
    from_agent: owning agent (the one that handled the request)
    to_agent: requesting agent (the original from_agent)
    approved: True = memories included; False = denied_reason is set
    memories: list of MemoryRecord (empty when approved=False)
    denied_reason: human-readable reason for denial (None when approved=True)
    """

    kind: str = "memory.access_response"
    request_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    approved: bool = False
    memories: list[MemoryRecord] = field(default_factory=list)
    denied_reason: Optional[str] = None

    def to_payload(self) -> dict:
        return {
            "kind": self.kind,
            "request_id": self.request_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "approved": self.approved,
            "memories": [m.to_dict() for m in self.memories],
            "denied_reason": self.denied_reason,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "MemoryAccessResponse":
        return cls(
            kind=payload.get("kind", "memory.access_response"),
            request_id=payload.get("request_id", ""),
            from_agent=payload.get("from_agent", ""),
            to_agent=payload.get("to_agent", ""),
            approved=bool(payload.get("approved", False)),
            memories=[
                MemoryRecord.from_dict(m)
                for m in payload.get("memories", [])
            ],
            denied_reason=payload.get("denied_reason"),
        )


# Default approval policy (v1): approve agent-scope from same-rack agents;
# deny client-scope unconditionally.
def default_approve(request: MemoryAccessRequest) -> tuple[bool, Optional[str]]:
    """Return (approved, denied_reason) using the v1 default policy."""
    if request.scope == "client":
        return False, "client-scope memory is siloed; no cross-client reads"
    if request.scope not in ("agent", "clan"):
        return False, f"unknown scope {request.scope!r}"
    return True, None
