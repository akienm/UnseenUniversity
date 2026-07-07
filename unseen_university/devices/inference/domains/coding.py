"""
coding.py — the coding task domain.

CodingDomain owns the coding domain's model selection + prompts + escalation policy. It
is the first registered Domain specialization (D-domain-object-encapsulation-2026-07-01).
Selection + the escalation walk are inherited behavior-preserving from BaseDomain; coding
adds two specializations: the advisory Critic runs for coding work, and the first loop
message is enriched with the orientation classifier's builder report (the relevant-files
map for code tickets).
"""

from __future__ import annotations

import logging
from pathlib import Path

from unseen_university.devices.inference.agentic_loop import LoopResult
from unseen_university.devices.inference.domains.base import BaseDomain

log = logging.getLogger(__name__)


def _orientation_prefix(ticket: dict) -> str:
    """Return the repo signature-map orientation block for a code ticket, or ''.

    D-coding-loop-redesign-aider-survey: replaces the bare relevant-FILES list (one symbol
    per file) with a signature MAP — several key symbol signatures per relevant file — so the
    model (and, post-split, the architect) can plan from structure without opening files to
    discover it. Fail-open: any exception → '' and the loop continues without it. The
    interface crossing (map size) is logged inside build_signature_map.
    """
    try:
        from unseen_university.devices.scraps.orientation_classifier import build_signature_map
        return build_signature_map(ticket)
    except Exception as exc:
        log.warning("CodingDomain signature-map failed for %s: %s", ticket.get("id", "?"), exc)
    return ""


class CodingDomain(BaseDomain):
    """The coding domain — model selection + prompts + escalation policy for coding tasks."""

    name = "coding"
    critic_enabled = True
    #: coding is the weak/local (minion) tier → windowed Read + edit-centric tools
    #: (T-coding-minion-aci-edit-centric). Generalist BaseDomain stays off (strong-tier passthrough).
    aci_mode = True
    #: run each attempt as the architect/editor split (D-coding-loop-redesign-aider-survey).
    #: A flag so the single-loop attempt can be restored without a code change (ticket rollback).
    architect_editor_enabled = True
    #: run the EDITOR phase as ONE completion of SEARCH/REPLACE blocks, applied deterministically
    #: (T-aider-port-editor-block-contract). Default OFF — the proven tool-loop editor stays the
    #: default until this path is proven; flip to restore/roll-forward without a code change.
    block_editor_enabled = False

    def _initial_message(self, ticket: dict) -> str:
        """Prepend the orientation builder report to the generalist ticket message."""
        return _orientation_prefix(ticket) + super()._initial_message(ticket)

    def _run_attempt(
        self,
        *,
        system_prompt: str,
        ticket: dict,
        ticket_id: str,
        agent_id: str,
        escalation_hop: int,
        prior_attempt: str,
        cwd: Path | None = None,
    ) -> LoopResult:
        """One coding attempt = the architect/editor split (or the single loop if disabled).

        Overrides BaseDomain._run_attempt to change ONLY what one attempt is; the escalation
        walk in BaseDomain.run is untouched and classifies the returned LoopResult identically.
        ``cwd`` (default None → the loop's _REPO_ROOT fallback) is the isolated working dir the
        edit tools run against — threaded to both the split flow and the single-loop fallback.
        """
        if not self.architect_editor_enabled:
            return super()._run_attempt(
                system_prompt=system_prompt, ticket=ticket, ticket_id=ticket_id,
                agent_id=agent_id, escalation_hop=escalation_hop, prior_attempt=prior_attempt,
                cwd=cwd,
            )
        from unseen_university.devices.inference.architect_editor import ArchitectEditorFlow

        return ArchitectEditorFlow(
            critic_enabled=self.critic_enabled,
            aci_mode=self.aci_mode,
            block_editor_enabled=self.block_editor_enabled,
        ).run(
            system_prompt=system_prompt,
            initial_message=self._initial_message(ticket),
            task_class=self.task_class,
            domain=self.name,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
            cwd=cwd,
        )
