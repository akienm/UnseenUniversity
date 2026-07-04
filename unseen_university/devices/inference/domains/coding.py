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

from unseen_university.devices.inference.agentic_loop import LoopResult
from unseen_university.devices.inference.domains.base import BaseDomain

log = logging.getLogger(__name__)


def _orientation_prefix(ticket: dict) -> str:
    """Return the orientation classifier's builder-report block for a code ticket, or ''.

    Fail-open: any exception → empty string, the loop continues without it. Interface
    crossing: INFO log with match count.
    """
    try:
        from unseen_university.devices.scraps.orientation_classifier import classify
        report = classify(ticket)
        if report.relevant_files:
            log.info("CodingDomain builder_report: %d relevant files for %s",
                     len(report.relevant_files), ticket.get("id", "?"))
            return report.to_text() + "\n\n"
    except Exception as exc:
        log.warning("CodingDomain builder_report failed for %s: %s", ticket.get("id", "?"), exc)
    return ""


class CodingDomain(BaseDomain):
    """The coding domain — model selection + prompts + escalation policy for coding tasks."""

    name = "coding"
    critic_enabled = True
    #: run each attempt as the architect/editor split (D-coding-loop-redesign-aider-survey).
    #: A flag so the single-loop attempt can be restored without a code change (ticket rollback).
    architect_editor_enabled = True

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
    ) -> LoopResult:
        """One coding attempt = the architect/editor split (or the single loop if disabled).

        Overrides BaseDomain._run_attempt to change ONLY what one attempt is; the escalation
        walk in BaseDomain.run is untouched and classifies the returned LoopResult identically.
        """
        if not self.architect_editor_enabled:
            return super()._run_attempt(
                system_prompt=system_prompt, ticket=ticket, ticket_id=ticket_id,
                agent_id=agent_id, escalation_hop=escalation_hop, prior_attempt=prior_attempt,
            )
        from unseen_university.devices.inference.architect_editor import ArchitectEditorFlow

        return ArchitectEditorFlow(critic_enabled=self.critic_enabled).run(
            system_prompt=system_prompt,
            initial_message=self._initial_message(ticket),
            task_class=self.task_class,
            domain=self.name,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
        )
