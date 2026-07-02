"""
capabilities — mixin-composable agent capabilities over Domain objects.

D-agent-capability-mixins-over-domains-2026-07-02 (stream A). A capability is a thin mixin
that HAS-A a Domain and exposes it by delegation (CapabilityMixin). Adding a task-capability
to an agent costs one Domain + one thin capability-mixin — never a bespoke agent subclass.
"""

from __future__ import annotations

from unseen_university.capabilities.base import CapabilityMixin
from unseen_university.capabilities.coding import CodingCapability
from unseen_university.capabilities.identity import IdentityMixin

__all__ = ["CapabilityMixin", "CodingCapability", "IdentityMixin"]
