"""
classifier/report.py — BuilderReport wire contract.

Shared schema for all downstream consumers of classifier output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BuilderReport:
    relevant_files: list[str] = field(default_factory=list)
    context_nodes: list[str] = field(default_factory=list)
    task_shape: str = ""
    confidence: float = 0.0
    classifier: str = ""
    stale: bool = False
    warnings: list[str] = field(default_factory=list)
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "relevant_files": self.relevant_files,
            "context_nodes": self.context_nodes,
            "task_shape": self.task_shape,
            "confidence": self.confidence,
            "classifier": self.classifier,
            "stale": self.stale,
            "warnings": self.warnings,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BuilderReport":
        return cls(
            relevant_files=d.get("relevant_files", []),
            context_nodes=d.get("context_nodes", []),
            task_shape=d.get("task_shape", ""),
            confidence=float(d.get("confidence", 0.0)),
            classifier=d.get("classifier", ""),
            stale=bool(d.get("stale", False)),
            warnings=list(d.get("warnings", [])),
            ts=d.get("ts", ""),
        )
