"""
base.py — CapabilityMixin: the thin seam that exposes a Domain's capability on an agent.

D-agent-capability-mixins-over-domains-2026-07-02 (stream A). A *capability* is a thin
mixin that HAS-A a Domain object and exposes it by delegation. It is NOT the Domain and
does NOT subclass one — the Domain already stands alone (D-domain-object-encapsulation);
re-fusing them would undo that split. The mixin carries NO task logic of its own: it holds
the domain name, resolves the Domain, and forwards a call to `Domain.run()`.

Composition contract:
  - Subclasses set `capability_domain` to a registered domain name (e.g. "coding").
  - The mixin resolves that name to a Domain object lazily, on first use, and caches it.
  - `run_capability(ticket, *, agent_id=..., urgency=...)` delegates to `Domain.run(...)`
    with the arguments passed through unchanged, and returns the domain's result verbatim.

Deliberately NO `__init__`: mixing this into a device must not add a cooperative-super()
step that a host's existing `__init__` chain could drop. The domain is resolved by a lazy
property instead, so the mixin composes onto any host (a plain object or a BaseDevice)
without touching construction.
"""

from __future__ import annotations

import logging

from unseen_university.devices.inference.domains import BaseDomain, resolve_domain

log = logging.getLogger(__name__)


class CapabilityMixin:
    """Thin mixin: HAS-A a Domain, exposes it by delegation. No task logic of its own.

    A host class mixes this in (alongside its device/agent bases) and sets
    `capability_domain` to the domain the capability wraps. `run_capability` is the single
    delegation surface — it forwards to the resolved Domain's `run()` and returns its
    result unchanged.
    """

    #: the registered domain name this capability exposes. Subclasses set it (e.g. "coding").
    #: '' resolves to the generalist BaseDomain (see resolve_domain) — a capability with no
    #: configured domain is a generalist passthrough, not an error.
    capability_domain: str = ""

    @property
    def _domain(self) -> BaseDomain:
        """The Domain object this capability wraps, resolved once and cached on the instance.

        Lazy + cached in `__dict__` so the mixin needs no `__init__` — nothing to cooperate
        with on the host's construction chain. HAS-A, not is-a: this returns a Domain the
        mixin holds; the mixin never becomes one.
        """
        cached = self.__dict__.get("_capability_domain_obj")
        if cached is None:
            cached = resolve_domain(self.capability_domain)
            self.__dict__["_capability_domain_obj"] = cached
        return cached

    def run_capability(
        self, ticket: dict, *, agent_id: str = "", urgency: str = "normal",
        cwd: "Path | None" = None,
    ) -> str | None:
        """Delegate a ticket to this capability's Domain and return its result unchanged.

        Pass-through of the Domain.run contract: `ticket`, `agent_id`, `urgency`, and `cwd` are
        forwarded verbatim (a dropped/renamed kwarg would silently change escalation
        behaviour — agent_id feeds the escalation walk / system_alarm; cwd isolates the
        edit-capable tools off the live repo). The mixin adds no logic beyond logging the
        crossing; the escalation walk lives in the Domain.
        """
        ticket_id = ticket.get("id", "?")
        # Interface crossing (capability → domain): one INFO line per delegation.
        log.info(
            "capability: delegating to domain=%s | ticket=%s | agent=%s | urgency=%s",
            self.capability_domain or "(generalist)", ticket_id, agent_id or "(none)", urgency,
        )
        return self._domain.run(ticket, agent_id=agent_id, urgency=urgency, cwd=cwd)
