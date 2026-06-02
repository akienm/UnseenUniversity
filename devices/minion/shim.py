"""
MinionShim — lifecycle for the minion worker device.

WorkerEnvelope: input (ticket + context).
WorkerResult:   output (DONE or ESCALATE signal + notes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


@dataclass
class WorkerEnvelope:
    """Input envelope for a single minion work request."""

    ticket_id: str
    description: str
    repo_map: str = ""
    session_id: str = ""
    # Appended on each escalation hop so higher tiers see the full history.
    escalation_history: list[dict] = field(default_factory=list)
    # Working directory for tool execution; empty = use ToolLoop default (cwd).
    cwd: str = ""
    # Inference tier: "minion" | "worker" | "analyst" | "designer".
    # Passed through to InferenceRequest so the rules engine picks the right model.
    task_class: str = "worker"


@dataclass
class WorkerResult:
    """Result envelope returned by MinionDevice.execute()."""

    # "DONE" | "ESCALATE: worker" | "ESCALATE: analyst" | "ESCALATE: designer"
    signal: str
    notes: str
    iterations: int = 0
    tools_called: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class MinionShim(BaseShim):
    """No external process to manage — lifecycle is a no-op."""

    @property
    def device_id(self) -> str:
        return "minion"

    def start(self) -> bool:
        log.info("MinionShim: started")
        return True

    def stop(self) -> bool:
        log.info("MinionShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        return {"passed": True, "details": "MinionShim: no external dependencies"}

    def rollback(self) -> None:
        log.info("MinionShim: rollback (no-op)")
