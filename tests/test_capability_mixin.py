"""Tests for CapabilityMixin + CodingCapability (T-capability-mixin-coding-capability).

The capability is the thin seam D-agent-capability-mixins-over-domains-2026-07-02 defines:
a mixin that HAS-A a Domain and exposes it by delegation. These tests exercise the NEW seam
directly (real red→green): they assert the delegation path is taken with arguments passed
through unchanged, and that the mixin composes (HAS-A, not is-a) without an __init__ that a
host's construction chain would have to cooperate with.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unseen_university.capabilities import CapabilityMixin, CodingCapability
from unseen_university.devices.inference.domains import BaseDomain, CodingDomain


class _Host(CodingCapability):
    """A plain host object mixing in the capability — stands in for an agent/device."""


def test_run_capability_delegates_to_domain_run():
    """run_capability forwards ticket + agent_id + urgency to Domain.run, unchanged, once,
    and returns its result verbatim — the seam is actually taken."""
    ticket = {"id": "T-demo", "title": "demo"}
    sentinel_result = "DONE: built the thing"

    fake_domain = MagicMock(spec=BaseDomain)
    fake_domain.run.return_value = sentinel_result

    host = _Host()
    # Patch the resolver the mixin uses so we spy the exact object it delegates to.
    with patch("unseen_university.capabilities.base.resolve_domain", return_value=fake_domain) as resolver:
        out = host.run_capability(ticket, agent_id="dicksimnel")

    # (a) delegated exactly once with the same ticket + agent_id (and urgency passed through).
    fake_domain.run.assert_called_once_with(ticket, agent_id="dicksimnel", urgency="normal")
    # (b) return value passed through unchanged.
    assert out is sentinel_result
    # the domain was resolved from the capability's configured name.
    resolver.assert_called_once_with("coding")


def test_urgency_is_forwarded_not_dropped():
    """A non-default urgency reaches Domain.run — a dropped kwarg would silently change policy."""
    fake_domain = MagicMock(spec=BaseDomain)
    fake_domain.run.return_value = None
    host = _Host()
    with patch("unseen_university.capabilities.base.resolve_domain", return_value=fake_domain):
        host.run_capability({"id": "T-x"}, agent_id="a", urgency="high")
    fake_domain.run.assert_called_once_with({"id": "T-x"}, agent_id="a", urgency="high")


def test_coding_capability_wraps_the_real_coding_domain():
    """Unpatched: CodingCapability HAS-A a CodingDomain resolved via resolve_domain('coding')."""
    domain = CodingCapability()._domain
    assert isinstance(domain, CodingDomain)
    assert domain.name == "coding"


def test_capability_is_has_a_not_is_a():
    """Compose, NOT is-a: a capability must not subclass a Domain (would re-fuse the split)."""
    assert not issubclass(CapabilityMixin, BaseDomain)
    assert not issubclass(CodingCapability, BaseDomain)


def test_mixin_defines_no_init():
    """The mixin adds no __init__ — nothing for a host's construction chain to cooperate with
    (the MRO-safety property that keeps composition onto a device drop-free)."""
    assert "__init__" not in CapabilityMixin.__dict__
    assert "__init__" not in CodingCapability.__dict__
