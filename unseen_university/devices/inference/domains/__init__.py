"""
domains — the Domain object registry (D-domain-object-encapsulation-2026-07-01).

A Domain is the single owner of a task-domain's model selection + prompts. `resolve_domain`
maps a domain name to its object: a registered name yields its specialized subclass; any
other name (including '') yields a BaseDomain carrying that name through — generalist
behavior, no crash, no name collapse. The passthrough keeps the Proxy swap behavior-
preserving: the selector sees the exact domain string it always did.
"""

from __future__ import annotations

import logging
import os

from unseen_university.devices.inference.domains.base import BaseDomain, DomainPrompts
from unseen_university.devices.inference.domains.coding import CodingDomain
from unseen_university.devices.inference.domains.general import GeneralDomain

log = logging.getLogger(__name__)

__all__ = ["BaseDomain", "CodingDomain", "DomainPrompts", "GeneralDomain", "resolve_domain"]

# Registered domain specializations, keyed by domain name. '' is the GENERAL domain — the
# default, from which everything else descends (Akien, 2026-07-08). It is a registered entry
# rather than a fallback special-case, so 'the default domain' is a thing you can point at.
_REGISTRY: dict[str, type[BaseDomain]] = {
    CodingDomain.name: CodingDomain,
    GeneralDomain.name: GeneralDomain,
}

# Truthy spellings for the harvest-mode operator toggle (case-insensitive).
_TRUTHY = {"1", "true", "yes", "on"}


def _harvest_mode_from_env() -> bool:
    """The operator on-switch for harvest mode (T-ds-harvest-mode-operator-toggle).

    A harvest session runs the DS process with UU_HARVEST_MODE set truthy; every domain that
    process resolves is then constructed with harvest_mode=True, so the escalation walk
    terminates at the fixed tier (see BaseDomain.run). Unset/empty/falsey = default OFF
    (production escalates as before). .select() ignores the flag, so a routing-side resolve is
    unaffected — only the .run() walk reads it.
    """
    return os.environ.get("UU_HARVEST_MODE", "").strip().lower() in _TRUTHY


def resolve_domain(name: str) -> BaseDomain:
    """Return the Domain object for `name`.

    A registered name yields its specialized subclass instance; '' yields the GeneralDomain
    (the default); any other name yields a GeneralDomain carrying that exact name. The name
    is passed through, never collapsed, so selection via the returned object is identical to
    calling the selector with that domain string directly.

    'The default is the general domain, and everybody else descends from that' — so an
    unregistered name is not an error and not a collapse to '': it is the general domain
    wearing that name, until someone writes the specialization.

    harvest_mode is set from the UU_HARVEST_MODE env toggle at this single construction
    chokepoint (default OFF); see ``_harvest_mode_from_env``.
    """
    harvest = _harvest_mode_from_env()
    if harvest:
        log.info("resolve_domain: harvest_mode ON (UU_HARVEST_MODE) for domain=%s", name or "(generalist)")
    cls = _REGISTRY.get(name)
    if cls is not None:
        return cls(harvest_mode=harvest)
    return GeneralDomain(name=name, harvest_mode=harvest)
