"""
Memory model - the fundamental unit of everything Igor knows.
Everything is a memory: facts, habits, core patterns, identity, role models.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class MemoryType(Enum):
    ROOT = "ROOT"
    CORE_PATTERN = "CORE_PATTERN"
    IDENTITY = "IDENTITY"
    ROLE_MODEL = "ROLE_MODEL"
    EPISODIC = "EPISODIC"       # Events that happened
    PROCEDURAL = "PROCEDURAL"   # How to do things (includes habits)
    INTERPRETIVE = "INTERPRETIVE"  # What things mean
    EXPERIENTIAL = "EXPERIENTIAL"  # Sequential emotional experiences
    FACTUAL = "FACTUAL"         # Objective information


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
}


@dataclass
class Memory:
    narrative: str
    memory_type: MemoryType
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    children_ids: list = field(default_factory=list)
    link_ids: list = field(default_factory=list)
    valence: float = 0.0
    arousal: float = 0.0      # G14 / #52: emotional profile — [-1,1] activated vs deactivated
    dominance: float = 0.0    # G14 / #52: emotional profile — [-1,1] in-control vs overwhelmed
    activation_count: int = 0
    friction_history: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

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
        return min(1.0, base + usage_boost + children_boost + friction_boost)

    @property
    def avg_friction(self) -> Optional[float]:
        if not self.friction_history:
            return None
        return sum(self.friction_history) / len(self.friction_history)

    @property
    def is_habit(self) -> bool:
        return (self.memory_type == MemoryType.PROCEDURAL
                and "trigger" in self.metadata)

    def __repr__(self):
        return f"Memory({self.id}, {self.memory_type.value}, inertia={self.inertia:.2f})"
