"""Domain-layer cwd isolation (T-ds-domain-cwd-isolation).

AgenticLoop's edit tools run against ``cwd or _REPO_ROOT``; the domain layer must be able to
thread an isolated working dir so an edit-capable harvest run does not bash/edit the live repo.
These tests assert ``BaseDomain.run(ticket, cwd=...)`` reaches ``_run_attempt`` (and thus the
loop) with that cwd — hermetic, no real inference (the recording attempt returns DONE at once).

PROOF NODE: run(ticket, cwd=tmp) threads tmp to _run_attempt. Red (run ignores cwd → None) → green.
"""
from __future__ import annotations

from unseen_university.devices.inference.domains.agentic_loop import LOOP_DONE, LoopResult
from unseen_university.devices.inference.domains.base import BaseDomain, DomainPrompts

_TICKET = {"id": "T-cwd", "title": "t", "tags": [], "description": "d"}


class _CwdRecordingDomain(BaseDomain):
    """Records the cwd its single attempt was handed, then returns DONE (no escalation walk)."""

    def __init__(self) -> None:
        super().__init__(name="")
        self.cwd_seen: object = "UNSET"

    @property
    def prompts(self) -> DomainPrompts:
        return DomainPrompts(system="test system")

    def _run_attempt(self, *, system_prompt, ticket, ticket_id, agent_id,
                     escalation_hop, prior_attempt, cwd=None) -> LoopResult:
        self.cwd_seen = cwd
        return LoopResult(LOOP_DONE, text="DONE: ok")


def test_run_threads_cwd_to_attempt(tmp_path):
    """PROOF: an explicit cwd reaches the attempt (and so the loop's edit tools use it)."""
    d = _CwdRecordingDomain()
    d.run(_TICKET, cwd=tmp_path)
    assert d.cwd_seen == tmp_path, f"cwd must thread to _run_attempt, got {d.cwd_seen!r}"


def test_run_without_cwd_threads_none():
    """Default: no cwd → None reaches the attempt (loop falls back to _REPO_ROOT, unchanged)."""
    d = _CwdRecordingDomain()
    d.run(_TICKET)
    assert d.cwd_seen is None
