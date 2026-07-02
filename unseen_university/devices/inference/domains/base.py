"""
base.py — the Domain object: one owner for a task-domain's model selection + prompts.

D-domain-object-encapsulation-2026-07-01. Domain-specificity was scattered — model
selection lived in rules_engine, prompts in domain_prompts.py, and no object *was*
'the coding domain'. A Domain unifies that: it owns (1) select() — the cost-optimizing,
availability-aware choice of a Source+ModelSpec for THIS domain, delegating to the
existing RulesEngine selector but OWNED here; and (2) prompts — the system (and, later,
loop) prompt text, resolved from the domain-prompt data store.

This ticket (T-domain-object-base) is behavior-preserving: the object wraps existing
selection + prompt behavior, relocated, with no change to what gets chosen. The agentic
loop + the single escalation owner move here in T-domain-owns-loop-and-escalation — that
ticket also revisits select()'s return shape (today a single RoutingDecision, matching
RulesEngine.route()).
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.devices.inference.domain_prompts import domain_prompt
from unseen_university.devices.inference.rules_engine import RoutingDecision, RulesEngine


@dataclass(frozen=True)
class DomainPrompts:
    """The prompt data a domain owns.

    `system` is the domain's system prompt ('' = generalist, so the caller keeps its own
    default). The `loop` prompt lands with the escalation-owner ticket; today only
    `system` is populated.
    """

    system: str = ""


class BaseDomain:
    """A task domain: owns model selection + prompts for one KIND of task.

    The base is the generalist / unspecialized domain. `name` is passed through to the
    selector's domain filter and to the prompt resolver, so an unregistered domain name
    behaves exactly as passing that name to RulesEngine.route today: a generalist request
    ('') matches any model; an unknown non-empty name resolves to no specialized prompt
    ('') and to whatever the domain-eligibility filter yields — no crash, no name
    collapse. Specialization is a registered subclass (see CodingDomain), not a name
    special-case.
    """

    #: the domain identifier; '' = generalist. Subclasses set their own.
    name: str = ""

    def __init__(self, name: str | None = None) -> None:
        if name is not None:
            self.name = name

    @property
    def prompts(self) -> DomainPrompts:
        """The domain's prompt data, resolved from the domain-prompt store by name."""
        return DomainPrompts(system=domain_prompt(self.name))

    def select(
        self,
        rules_engine: RulesEngine,
        *,
        task_class: str = "worker",
        session_id: str = "",
        hour: int | None = None,
        foreground: bool = False,
        urgency: str | None = None,
        required_features: list[str] | None = None,
        required_difficulty: str = "",
    ) -> RoutingDecision | None:
        """Choose the Source+ModelSpec for this domain — cost-optimizing, availability-aware.

        Delegates to the existing RulesEngine selector, supplying this domain's `name` as
        the domain filter; the domain OWNS the call. Behavior-preserving: identical to
        calling rules_engine.route(..., domain=self.name) directly. Returns a single
        RoutingDecision (or None if nothing is available) — the ordered-candidates form
        arrives with the escalation-owner ticket (T-domain-owns-loop-and-escalation).
        """
        return rules_engine.route(
            task_class=task_class,
            session_id=session_id,
            hour=hour,
            foreground=foreground,
            urgency=urgency,
            required_features=required_features,
            domain=self.name,
            required_difficulty=required_difficulty,
        )
