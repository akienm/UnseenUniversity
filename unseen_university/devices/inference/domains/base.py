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

import logging
from dataclasses import dataclass

from unseen_university.devices.inference.agentic_loop import (
    LOOP_AVAILABILITY,
    LOOP_COST_EXCEEDED,
    LOOP_DONE,
    AgenticLoop,
    LoopResult,
    NativeToolCodec,
)
from unseen_university.devices.inference.domain_prompts import domain_prompt
from unseen_university.devices.inference.rules_engine import RoutingDecision, RulesEngine

log = logging.getLogger(__name__)


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
    #: the a-priori difficulty tier this domain's work starts at (the walk bumps UP from here).
    task_class: str = "worker"
    #: whether the shared loop runs the advisory Critic for this domain (coding: yes).
    critic_enabled: bool = False
    #: bounded source-down retries at the SAME difficulty before the walk halts. Small and
    #: hard: an infra blip re-selects next-cheapest, a persistent outage must not loop (and
    #: must never walk onto paid tiers unbounded).
    max_availability_retries: int = 2

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

    # ── The escalation walk: this domain is the SINGLE escalation owner ────────

    def run(self, ticket: dict, *, urgency: str = "normal", agent_id: str = "") -> str | None:
        """Work a ticket through the shared agentic loop, driving THIS domain's escalation walk.

        The domain is the one escalation owner (D-domain-object-encapsulation): it supplies
        prompts + the escalation policy to the shared AgenticLoop and reads the loop's typed
        outcome. The money-safety walk (relocated from DS._run_inference, semantics identical):

          - CAPABILITY failure (reached a terminal but never DONE: escalate/max-turns/prose):
            bump difficulty ONE rung and re-run the domain-aware selector for a more-capable
            (pricier) tier. The ONLY trigger that spends up.
          - AVAILABILITY failure (no live source reached): NOT escalation — re-select the
            next-cheapest at the SAME difficulty (bounded), 'Hex-DOWN is not a branch'. A
            system_alarm fires if retries exhaust.
          - Past the top difficulty rung: inference failure → system_alarm → HALT. Checked
            BEFORE dispatch so the walk terminates cleanly and never loops.
          - COST_EXCEEDED (a paid run hit its per-run cost cap): halt — a pricier tier costs more.

        Returns the DONE result text, or None to HALT (a system_alarm has fired). Every hop
        logs WHICH step failed (loop/select/escalate) at the crossing.
        """
        from unseen_university.devices.inference.routing_buckets import (
            bump_difficulty,
            task_class_to_difficulty,
        )
        from unseen_university import system_alarms

        ticket_id = ticket.get("id", "?")
        system_prompt = self.prompts.system
        base_difficulty = task_class_to_difficulty(self.task_class)
        escalation_hop = 0
        prior_attempt = ""
        availability_retries = 0

        while True:
            # Terminal check BEFORE dispatch: bumped past the top rung → inference failure.
            required = bump_difficulty(base_difficulty, escalation_hop)
            if required is None:
                system_alarms.raise_alarm(
                    signature=f"inference-capability-ceiling:{ticket_id}",
                    caller=self.name or "domain",
                    message=(
                        f"capability ceiling for ticket {ticket_id}: escalated past the top "
                        f"difficulty tier ('{base_difficulty}'+{escalation_hop}) and still no DONE "
                        f"— inference failure, halting for analysis"
                    ),
                    fatal=False,
                )
                log.error("domain=%s crossing|step=escalate|ticket=%s|hop=%d|result=capability-ceiling-halt",
                          self.name, ticket_id, escalation_hop)
                return None

            log.info("domain=%s crossing|step=loop|ticket=%s|hop=%d|difficulty=%s",
                     self.name, ticket_id, escalation_hop, required)
            try:
                result = self._run_attempt(
                    system_prompt=system_prompt,
                    ticket=ticket,
                    ticket_id=ticket_id,
                    agent_id=agent_id,
                    escalation_hop=escalation_hop,
                    prior_attempt=prior_attempt,
                )
            except Exception as exc:
                # Defense in depth: the loop returns AVAILABILITY rather than raising, but any
                # unexpected raise is treated as availability (re-select), never a paid bump.
                log.error("domain=%s: loop raised for %s (hop=%d): %s",
                          self.name, ticket_id, escalation_hop, exc)
                result = LoopResult(LOOP_AVAILABILITY, text=str(exc))

            cls = self._classify(result)
            log.info("domain=%s crossing|step=classify|ticket=%s|hop=%d|outcome=%s|class=%s",
                     self.name, ticket_id, escalation_hop, result.outcome, cls)

            if cls == "done":
                log.info("domain=%s: DONE for %s at hop=%d difficulty=%s",
                         self.name, ticket_id, escalation_hop, required)
                return result.text

            if cls == "cost":
                system_alarms.raise_alarm(
                    signature=f"inference-cost-cap:{ticket_id}",
                    caller=self.name or "domain",
                    message=(
                        f"cost cap hit for ticket {ticket_id} without completing — halting "
                        f"(bumping to a pricier tier would only cost more)"
                    ),
                    fatal=False,
                )
                log.error("domain=%s crossing|step=loop|ticket=%s|result=cost-cap-halt: %s",
                          self.name, ticket_id, (result.text or "").strip()[:120])
                return None

            if cls == "availability":
                # A MID-RUN availability wall is not a transient start-up blip. When the loop
                # did productive turns and THEN a source timeout/down killed it (2026-07-03
                # observe-run: a 120s dispatch timeout ~mid-loop at hop 0), re-running the
                # whole loop from turn 0 re-does all that work and hits the same wall — the
                # doomed identical retry the ticket targets (burned 3× before halting on a
                # LYING 'availability-exhausted'). Halt honestly, naming the true cause. A
                # tier-bump is NOT the honest escalation here: 'code'→'design' has no live
                # local source, so it would degrade straight back into no-source (CP3). Only
                # turns==0 (the loop never completed a turn = source never came up) is a real
                # transient blip that a cheap bounded retry may clear — that path is kept.
                if result.turns > 0:
                    system_alarms.raise_alarm(
                        signature=f"inference-availability-midrun-wall:{ticket_id}",
                        caller=self.name or "domain",
                        message=(
                            f"ticket {ticket_id} hit an availability/timeout wall after "
                            f"{result.turns} productive turn(s) at difficulty '{required}' — "
                            f"re-running the full loop would re-do that work and hit the same "
                            f"wall; halting honestly instead of blind-retrying an identical "
                            f"path (no more-capable local source to bump to)"
                        ),
                        fatal=False,
                    )
                    log.error("domain=%s crossing|step=select|ticket=%s|turns=%d|"
                              "result=availability-midrun-wall-halt",
                              self.name, ticket_id, result.turns)
                    return None
                availability_retries += 1
                if availability_retries > self.max_availability_retries:
                    system_alarms.raise_alarm(
                        signature=f"inference-availability-exhausted:{ticket_id}",
                        caller=self.name or "domain",
                        message=(
                            f"no live source for ticket {ticket_id} after "
                            f"{self.max_availability_retries} retries at difficulty '{required}' — halting"
                        ),
                        fatal=False,
                    )
                    log.error("domain=%s crossing|step=select|ticket=%s|result=availability-exhausted-halt",
                              self.name, ticket_id)
                    return None
                log.warning("domain=%s crossing|step=select|ticket=%s|retry=%d/%d|difficulty=%s|reason=availability",
                            self.name, ticket_id, availability_retries, self.max_availability_retries, required)
                continue  # same hop → selector skips the down source, picks next-cheapest

            # cls == 'capability': reached a terminal but never DONE → bump difficulty one rung.
            prior_attempt = (result.text or "").strip()[:400]
            escalation_hop += 1
            log.info("domain=%s crossing|step=escalate|ticket=%s|hop→%d|reason=capability",
                     self.name, ticket_id, escalation_hop)

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
        """Run ONE attempt for the current hop and return its typed LoopResult.

        The base attempt is a single shared AgenticLoop — behavior-preserving, exactly what
        BaseDomain.run ran inline before (D-coding-loop-redesign extracted it). A domain
        overrides this to change WHAT one attempt is (CodingDomain runs the architect/editor
        split) WITHOUT touching the escalation walk that classifies the result — the walk
        stays the single money-safety owner (availability re-selects, capability bumps).
        """
        return AgenticLoop(
            codec=NativeToolCodec(), critic_enabled=self.critic_enabled,
        ).run(
            system_prompt=system_prompt,
            initial_message=self._initial_message(ticket),
            task_class=self.task_class,
            domain=self.name,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
        )

    def _classify(self, result: LoopResult) -> str:
        """Map a typed LoopResult to the escalation policy's class.

        The availability-vs-capability split is the whole safety of the walk — capability
        bumps to a pricier tier, availability must NOT (it re-selects at the same difficulty).
        """
        if result.outcome == LOOP_DONE:
            return "done"
        if result.outcome == LOOP_COST_EXCEEDED:
            return "cost"
        if result.outcome == LOOP_AVAILABILITY:
            return "availability"
        return "capability"  # escalate / max_turns / error: the tier could not finish

    def _initial_message(self, ticket: dict) -> str:
        """Build the first user message for the loop from a ticket dict (generalist form)."""
        ticket_id = ticket.get("id", "?")
        return (
            f"Ticket ID: {ticket_id}\n"
            f"Title: {ticket.get('title', 'No title')}\n"
            f"Tags: {', '.join(ticket.get('tags', []))}\n\n"
            f"Description:\n{ticket.get('description', ticket.get('title', ''))}"
        )
