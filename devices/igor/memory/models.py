"""
Memory model - the fundamental unit of everything Igor knows.
Everything is a memory: facts, habits, core patterns, identity, role models.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class MemoryType(Enum):
    ROOT = "ROOT"
    CORE_PATTERN = "CORE_PATTERN"
    IDENTITY = "IDENTITY"
    ROLE_MODEL = "ROLE_MODEL"
    EPISODIC = "EPISODIC"  # Events that happened
    PROCEDURAL = "PROCEDURAL"  # How to do things (includes habits)
    INTERPRETIVE = "INTERPRETIVE"  # What things mean
    EXPERIENTIAL = "EXPERIENTIAL"  # Sequential emotional experiences
    FACTUAL = "FACTUAL"  # Objective information
    REFERENCE = (
        "REFERENCE"  # #65: tagged blob — brief narrative + full content in memory_blobs
    )
    CREDENTIAL_REF = (
        "CREDENTIAL_REF"  # #71: credential pointer — what exists + where, NOT the value
    )
    GOAL = "GOAL"  # D275: active goal node — TACTICAL (completable) or STRATEGIC (orientation)


class MemoryScope(Enum):
    CLASS = "class"  # shared across all instances — ROOT, CORE_PATTERN, IDENTITY, FACTUAL, etc.
    INSTANCE = "instance"  # instance-local — EPISODIC, EXPERIENTIAL, CREDENTIAL_REF
    SESSION = "session"  # ephemeral — cleared at session end (reserved)


# Memory types that are scoped to a specific instance rather than shared across the class
_INSTANCE_SCOPE_TYPES = {
    MemoryType.EPISODIC,
    MemoryType.EXPERIENTIAL,
    MemoryType.CREDENTIAL_REF,
    MemoryType.GOAL,  # D275: active goals are instance-scoped — each Igor has its own
}


def default_scope(memory_type: MemoryType) -> MemoryScope:
    """Return the default MemoryScope for a given memory_type."""
    return (
        MemoryScope.INSTANCE
        if memory_type in _INSTANCE_SCOPE_TYPES
        else MemoryScope.CLASS
    )


# Base inertia by type - network position, activation, and friction adjust these
BASE_INERTIA = {
    MemoryType.ROOT: 1.0,
    MemoryType.CORE_PATTERN: 0.95,
    MemoryType.IDENTITY: 0.85,
    MemoryType.ROLE_MODEL: 0.75,
    MemoryType.EPISODIC: 0.20,
    MemoryType.PROCEDURAL: 0.30,
    MemoryType.INTERPRETIVE: 0.25,
    MemoryType.EXPERIENTIAL: 0.20,
    MemoryType.FACTUAL: 0.25,
    MemoryType.REFERENCE: 0.40,  # blobs are intentionally stored — higher base inertia
    MemoryType.CREDENTIAL_REF: 0.50,  # credential refs are stable until env changes
    MemoryType.GOAL: 0.15,  # D275: goals are ephemeral — low base inertia, kept hot via TWM
}


@dataclass
class Memory:
    narrative: str
    memory_type: MemoryType
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    children_ids: list = field(default_factory=list)
    link_ids: list = field(default_factory=list)  # legacy — kept for migration compat
    links: dict = field(
        default_factory=dict
    )  # #128: outgoing directed weighted edges {id: weight}
    valence: float = 0.0
    arousal: float = (
        0.0  # G14 / #52: emotional profile — [-1,1] activated vs deactivated
    )
    dominance: float = (
        0.0  # G14 / #52: emotional profile — [-1,1] in-control vs overwhelmed
    )
    activation_count: int = 0
    friction_history: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    last_accessed: Optional[datetime] = None  # #128: last activation timestamp
    metadata: dict = field(default_factory=dict)
    portable: bool = (
        True  # #71: False = instance-local (machine paths, episodic, credentials)
    )
    # G46: provenance + epistemic fields
    source: str = (
        ""  # where this memory came from: genesis | cloud_directed | interaction | reading | user_seeded | self_edit | ""
    )
    confidence: float = (
        1.0  # 0.0–1.0: how certain is this memory? (1.0 = fully trusted)
    )
    context_of_encoding: str = (
        ""  # brief context at creation: what was happening when this was encoded?
    )
    # #123: scope — class/instance/session; set by __post_init__ from memory_type if not provided
    scope: Optional["MemoryScope"] = None
    # D260: engram program payload — triggers dict + named cells + data fields
    # payload.NARRATIVE is the canonical embedding source (falls back to self.narrative)
    payload: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.scope is None:
            self.scope = default_scope(self.memory_type)

    @property
    def embedding_text(self) -> str:
        """D260: canonical text for embedding — payload.NARRATIVE if present, else narrative."""
        if self.payload and self.payload.get("NARRATIVE"):
            return self.payload["NARRATIVE"]
        return self.narrative

    @property
    def inertia(self) -> float:
        """Inertia emerges from network position, not declaration."""
        base = BASE_INERTIA.get(self.memory_type, 0.25)
        usage_boost = min(0.10, self.activation_count * 0.002)
        children_boost = min(0.10, len(self.children_ids) * 0.01)
        if self.friction_history:
            avg = sum(self.friction_history) / len(self.friction_history)
            friction_boost = max(0.0, (1.0 - avg) * 0.05)
        else:
            friction_boost = 0.0
        # #66: amygdala analog — high arousal or strong valence at encoding → more durable
        # abs(arousal) > 0.5 adds up to +0.08; emotionally_charged flag adds +0.05
        arousal_boost = (
            min(0.08, abs(self.arousal) * 0.16) if abs(self.arousal) > 0.3 else 0.0
        )
        charged_boost = 0.05 if self.metadata.get("emotionally_charged") else 0.0
        return min(
            1.0,
            base
            + usage_boost
            + children_boost
            + friction_boost
            + arousal_boost
            + charged_boost,
        )

    @property
    def avg_friction(self) -> Optional[float]:
        if not self.friction_history:
            return None
        return sum(self.friction_history) / len(self.friction_history)

    @property
    def is_habit(self) -> bool:
        # #128: any memory with a trigger can be a habit — not gated on PROCEDURAL type
        return bool(self.metadata.get("trigger"))

    def __repr__(self):
        return (
            f"Memory({self.id}, {self.memory_type.value}, inertia={self.inertia:.2f})"
        )
