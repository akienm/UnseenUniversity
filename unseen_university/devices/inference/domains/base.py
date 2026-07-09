"""
base.py — the Domain object: one owner for a task-domain's EXECUTION and prompts.

D-domain-object-encapsulation-2026-07-01. A Domain owns what it means to *do* a kind of
work: (1) the agentic loop / attempt shape, (2) the escalation walk (the single money-safety
owner: capability failures bump difficulty, availability failures re-select at the same
difficulty), and (3) prompts, resolved from the domain-prompt store.

A domain does NOT route. It is a CONSUMER of the inference proxy — it runs a loop that
dispatches to it. Routing lives in the routing layer (dimensions.route_request +
rules_engine.resolve), and `domain` reaches the resolver as a plain dimension string. The old
BaseDomain.select() inverted this: the proxy had to import a domain in order to choose a
model, which made `device -> domains -> agentic_loop -> device` a cycle (hidden behind a
function-local lazy import). Deleted in T-inference-break-proxy-domain-cycle.

Layering, one direction only:  worker -> domain -> inference proxy (routing + dispatch).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from unseen_university.devices.inference.domains.agentic_loop import (
    LOOP_AVAILABILITY,
    LOOP_COST_EXCEEDED,
    LOOP_DONE,
    AgenticLoop,
    LoopResult,
    NativeToolCodec,
)
from unseen_university.devices.inference.domains.domain_prompts import domain_prompt
from unseen_university.devices.inference.domains.reply_text import conclusion

log = logging.getLogger(__name__)

#: The exact phrase that tells the next rung its predecessor FAILED. A handoff without it reads
#: as helpful prior work, and the stronger model continues the weaker one's reasoning.
#: Measured live (T-escalation-handoff-transmits-the-confabulation): deepseek-r1:32b answers
#: b4-boxes correctly alone, and adopts deepseek-r1:14b's wrong answer when handed its
#: unmarked scratchpad. Kept as a constant so the test asserts the marker, not a loose word
#: like "wrong" — which the weak model's own rambling happens to contain.
FAILED_MARKER = "did not satisfy the completion check"

@dataclass(frozen=True)
class DomainPrompts:
    """The prompt data a domain owns.

    `system` is the domain's system prompt ('' = generalist, so the caller keeps its own
    default). The `loop` prompt lands with the escalation-owner ticket; today only
    `system` is populated.
    """

    system: str = ""


class BaseDomain:
    """A task domain: owns execution (loop + escalation walk) and prompts for one KIND of task.

    The base is the generalist / unspecialized domain. `name` reaches the resolver as a plain
    domain dimension on the dispatched request, and resolves this domain's prompt: an
    unregistered domain name is not a crash and not a name collapse — a generalist request
    ('') matches any model, and an unknown non-empty name yields no specialized prompt ('')
    plus whatever the domain-eligibility filter allows. Specialization is a registered
    subclass (see CodingDomain), not a name special-case.
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
    #: minion-tier ACI (windowed Read + edit-centric tools) for this domain's attempts. The
    #: generalist base is a strong-tier passthrough → off; CodingDomain (weak local tier) → on.
    aci_mode: bool = False
    #: harvest-testing mode (T-ds-harvest-mode-escalation-off). When True, a CAPABILITY wall
    #: does NOT bump to a pricier tier — the escalation walk terminates at the fixed tier so the
    #: wall itself is the harvested signal (the builder starve-curve wants 'the cheap tier
    #: couldn't', unmixed with 'a stronger one could'). Default False = production escalates as
    #: before. A FLAG, not deletion: the walk below is untouched, only gated at the bump point.
    #: The operator on-switch (turning this on for a real harvest session) is deferred to the
    #: stuck-ladder ticket that consumes the wall; here the flag is set explicitly (proof + the
    #: next ticket's driver).
    harvest_mode: bool = False

    def __init__(self, name: str | None = None, *, harvest_mode: bool = False) -> None:
        if name is not None:
            self.name = name
        self.harvest_mode = harvest_mode

    @property
    def prompts(self) -> DomainPrompts:
        """The domain's prompt data, resolved from the domain-prompt store by name."""
        return DomainPrompts(system=domain_prompt(self.name))

    # NOTE: this object deliberately has NO select()/routing method. A domain CONSUMES the
    # inference proxy (it runs an agentic loop that dispatches); it is not something the proxy
    # calls to choose a model. The old BaseDomain.select() was a pure pass-through that built
    # a RouteRequest from the domain's own name — which forced device.py to import a domain in
    # order to route, making device -> domains -> agentic_loop -> device a cycle. Routing now
    # lives entirely in the routing layer (dimensions.route_request + rules_engine.resolve);
    # `domain` is just a dimension string the proxy already has on the request.
    # (T-inference-break-proxy-domain-cycle.)

    # ── The escalation walk: this domain is the SINGLE escalation owner ────────

    def run(
        self, ticket: dict, *, urgency: str = "normal", agent_id: str = "",
        cwd: Path | None = None,
    ) -> str | None:
        """Work a ticket through the shared agentic loop, driving THIS domain's escalation walk.

        ``cwd`` (default None → the loop's ``_REPO_ROOT`` fallback) is the working directory the
        edit-capable tools run against; pass an isolated dir to run a build off the live repo
        (T-ds-domain-cwd-isolation — required for a harvest run, whose model would otherwise
        bash/edit the live tree).

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
          - HARVEST MODE (self.harvest_mode): a CAPABILITY failure does NOT bump — the walk
            terminates at the fixed tier (escalation_hop stays 0), returning None with NO
            system_alarm (the wall is the wanted outcome, not an incident). The worker_listener
            declines the ticket back to sprint. Testing-phase flag; production stays escalating.

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

        if self.harvest_mode:
            log.info(
                "domain=%s: harvest_mode=on — escalation disabled, fixed tier ('%s') | ticket=%s",
                self.name or "(generalist)", base_difficulty, ticket_id,
            )

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
                    cwd=cwd,
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

            # cls == 'capability': reached a terminal but never DONE.
            # NB the prior-attempt handoff is built by _summarize_attempt (below), NOT by
            # slicing result.text — see that method for what the naive slice actually sent.
            if self.harvest_mode:
                # Harvest mode: do NOT escalate. Hand the wall to the cost-ordered stuck-ladder,
                # which picks the cheapest viable rung (answer / drop / halt / call-CC) and records
                # the choice — the distribution over rungs IS the builder starve-curve. Terminal
                # stays None: rung 1's answer is data-starved today and a bare answer string would
                # fail the completion gate → escalate (see stuck_ladder module doc); the rung-choice
                # RECORD is what distinguishes call-CC from halt from drop. No system_alarm — a
                # harvested wall is the wanted outcome, not an incident.
                from unseen_university.devices.inference.domains.stuck_ladder import (
                    DEFAULT_DOMAIN,
                    StuckEvent,
                    StuckLadder,
                )

                choice = StuckLadder().resolve(StuckEvent(
                    ticket_id=ticket_id, tier=required, turn_reached=result.turns,
                    domain=self.name or DEFAULT_DOMAIN,
                ))
                log.info("domain=%s crossing|step=escalate|ticket=%s|hop=%d|"
                         "result=harvest-wall|rung=%s|reason=capability (escalation disabled)",
                         self.name, ticket_id, escalation_hop, choice.rung)
                return None
            # otherwise bump difficulty one rung.
            prior_attempt = self._summarize_attempt(result)
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
        cwd: Path | None = None,
    ) -> LoopResult:
        """Run ONE attempt for the current hop and return its typed LoopResult.

        The base attempt is a single shared AgenticLoop — behavior-preserving, exactly what
        BaseDomain.run ran inline before (D-coding-loop-redesign extracted it). A domain
        overrides this to change WHAT one attempt is (CodingDomain runs the architect/editor
        split) WITHOUT touching the escalation walk that classifies the result — the walk
        stays the single money-safety owner (availability re-selects, capability bumps).
        """
        return AgenticLoop(
            codec=NativeToolCodec(), critic_enabled=self.critic_enabled, aci_mode=self.aci_mode,
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

    def _summarize_attempt(self, result: LoopResult) -> str:
        """What a FAILED attempt hands to the next rung up. The seam a domain may override.

        Two things this must get right, both learned the hard way
        (T-escalation-handoff-transmits-the-confabulation, measured on the live rack):

        1. THE CONCLUSION, NOT THE SCRATCHPAD. The old code was
           ``(result.text or "").strip()[:400]`` — the FIRST 400 characters. A reasoning model
           emits ``<think>…scratchpad…</think>`` and only then its answer, so that slice is the
           opening of the scratchpad, cut off mid-sentence. ``conclusion()`` strips the
           reasoning and takes the TAIL.

        2. SAY IT FAILED. The proxy injects this under a "**What was tried:**" header, which
           reads as useful prior work. Handed deepseek-r1:14b's unmarked scratchpad,
           deepseek-r1:32b abandoned its own correct answer and adopted the weak model's wrong
           one; handed the same conclusion explicitly marked failed, it stayed correct. So the
           escalation walk — which is the only thing that KNOWS the attempt failed — says so
           here, rather than trusting the proxy's framing.

        An attempt whose reasoning never terminated (truncated <think>) has no conclusion to
        pass on, and passing on the scratchpad is worse than passing on nothing.
        """
        # STUB (proof-first): the shipped behaviour — the FIRST 400 characters of the raw
        # reply, scratchpad and all, with nothing saying the attempt failed.
        return (result.text or "").strip()[:400]

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
