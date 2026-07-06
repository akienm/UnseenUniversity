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

log = logging.getLogger(__name__)

__all__ = ["BaseDomain", "CodingDomain", "DomainPrompts", "resolve_domain"]

# Registered domain specializations, keyed by domain name.
_REGISTRY: dict[str, type[BaseDomain]] = {
    CodingDomain.name: CodingDomain,
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

    A registered name yields its specialized subclass instance; any other name — unknown
    or '' (generalist) — yields a BaseDomain carrying that exact name. The name is passed
    through, never collapsed, so selection via the returned object is identical to calling
    the selector with that domain string directly.

    harvest_mode is set from the UU_HARVEST_MODE env toggle at this single construction
    chokepoint (default OFF); see ``_harvest_mode_from_env``.
    """
    harvest = _harvest_mode_from_env()
    if harvest:
        log.info("resolve_domain: harvest_mode ON (UU_HARVEST_MODE) for domain=%s", name or "(generalist)")
    cls = _REGISTRY.get(name)
    if cls is not None:
        return cls(harvest_mode=False)
    return BaseDomain(name=name, harvest_mode=False)
