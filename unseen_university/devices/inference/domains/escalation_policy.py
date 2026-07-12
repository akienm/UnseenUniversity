"""
escalation_policy.py — escalation as DATA, not control flow scattered across the walk.

D-domains-general-with-device-owned-specializations-2026-07-08 (Amendment 2): the DOMAIN is
the escalation DECIDER, and the GENERAL domain owns the DEFAULT. This object is what a domain
carries to say HOW its capability walls are handled. It collapses two ad-hoc flags into one:

  - BaseDomain.harvest_mode (a bool gating the capability bump) → the HARVEST policy.
  - The pin-to-one-rung determinism that RouteRequest.escalation_allowed=False gave the
    resolver → the NO_ESCALATION policy at the walk layer (the walk never bumps the hop, so it
    never sends a raised difficulty — deterministic without a resolver flag; that flag is
    retired as redundant, T-inference-escalation-policy-object).

The ticket frames a policy as ladder + bump-rule + terminal. Here:
  - ladder    the shared difficulty rungs (routing_buckets.bump_difficulty) — ONE ladder for
              all policies. A per-policy ladder is a PROMOTION CANDIDATE (DSCoding), not built
              until an experiment actually needs a different one (anti-ceremony; the decision's
              own test).
  - bump rule `escalates`: does a CAPABILITY wall advance a rung?
  - terminal  `on_wall`: what happens when the walk can go no further — because it does not
              escalate (escalates=False, at hop 0) OR because the shared ladder ran out
              (escalates=True, past the top rung).

ONLY the capability-wall disposition is policy-varying. Availability (retry the same rung,
bounded) and cost-cap (halt) handling are UNIVERSAL money-safety behavior and are NOT policy
axes — encoding them here would be ceremony and would risk silently changing money safety.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Terminal dispositions for a capability wall the walk cannot escalate past.
#: CEILING — fire the capability-ceiling system_alarm and HALT. The DEFAULT terminal: the
#:   ladder ran out and nothing DONE is a genuine inference failure worth an incident.
ON_WALL_CEILING = "ceiling"
#: SILENT — HALT with NO alarm. NO_ESCALATION's terminal: a pinned, deterministic single shot
#:   (proofs, deterministic tests). A capability failure at a deliberately pinned rung is the
#:   asked-for result, not an incident.
ON_WALL_SILENT = "silent"
#: HARVEST — route the wall to the cost-ordered stuck-ladder and HALT with NO alarm. HARVEST's
#:   terminal: the wall IS the wanted signal (the builder starve-curve), not an incident.
ON_WALL_HARVEST = "harvest"


@dataclass(frozen=True)
class EscalationPolicy:
    """How a domain's escalation walk handles a capability wall.

    Two orthogonal axes, and nothing else — the shared ladder and the universal
    availability/cost handling live in the walk, not here.

    ``escalates``  — True: a capability wall advances one rung up the shared ladder (spends up).
                     False: the walk stays at the seed rung (hop never leaves 0).
    ``on_wall``    — the terminal, one of ON_WALL_*: what happens when the walk can go no
                     further (escalates=False at hop 0, or escalates=True past the top rung).
    """

    name: str
    escalates: bool
    on_wall: str


#: The standard ladder everyone inherits: a capability wall spends up a rung; past the top
#: rung is a capability-ceiling incident. Chat and anything that does not care get this free
#: (Amendment 2: the default IS the general domain).
DEFAULT_POLICY = EscalationPolicy(name="default", escalates=True, on_wall=ON_WALL_CEILING)

#: Pin to one rung, never spend up, and halt SILENTLY on a capability wall — for proofs and
#: deterministic tests. The degenerate policy the retired escalation_allowed=False expressed.
NO_ESCALATION_POLICY = EscalationPolicy(name="no_escalation", escalates=False, on_wall=ON_WALL_SILENT)

#: Fixed tier; a capability wall is the harvested signal, routed to the stuck-ladder with no
#: alarm — the builder starve-curve. The policy the retired harvest_mode bool expressed.
HARVEST_POLICY = EscalationPolicy(name="harvest", escalates=False, on_wall=ON_WALL_HARVEST)
