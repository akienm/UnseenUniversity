"""
domains — the Domain object registry (D-domain-object-encapsulation-2026-07-01).

A Domain is the single owner of a task-domain's model selection + prompts. `resolve_domain`
maps a domain name to its object: a registered name yields its specialized subclass; any
other name (including '') yields a BaseDomain carrying that name through — generalist
behavior, no crash, no name collapse. The passthrough keeps the Proxy swap behavior-
preserving: the selector sees the exact domain string it always did.
"""

from __future__ import annotations

from unseen_university.devices.inference.domains.base import BaseDomain, DomainPrompts
from unseen_university.devices.inference.domains.coding import CodingDomain

__all__ = ["BaseDomain", "CodingDomain", "DomainPrompts", "resolve_domain"]

# Registered domain specializations, keyed by domain name.
_REGISTRY: dict[str, type[BaseDomain]] = {
    CodingDomain.name: CodingDomain,
}


def resolve_domain(name: str) -> BaseDomain:
    """Return the Domain object for `name`.

    A registered name yields its specialized subclass instance; any other name — unknown
    or '' (generalist) — yields a BaseDomain carrying that exact name. The name is passed
    through, never collapsed, so selection via the returned object is identical to calling
    the selector with that domain string directly.
    """
    cls = _REGISTRY.get(name)
    if cls is not None:
        return cls()
    return BaseDomain(name=name)
