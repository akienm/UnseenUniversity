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

    def _initial_message(self, ticket: dict) -> str:
        """Prepend the orientation builder report to the generalist ticket message."""
        return _orientation_prefix(ticket) + super()._initial_message(ticket)
