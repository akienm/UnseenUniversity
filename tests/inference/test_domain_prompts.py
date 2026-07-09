"""
Tests for T-inference-domain-prompt: the system prompt is DATA keyed by domain.

The router routes BOTH model and prompt by domain (Intention-Based Development).
The DS 'coding' builder prompt moved VERBATIM into the domain-prompt store; DS
resolves it by domain. Byte-identity is scoped to the domain-prompt text.
"""

from __future__ import annotations

import hashlib

from unseen_university.devices.inference.domains.domain_prompts import domain_prompt

# Anchor: sha256 of the DS builder/coding system prompt as it stood before the move
# (device.py SYSTEM_PROMPT, len 2944). The move must be byte-identical — this pin
# fails loudly on any drift.
_CODING_PROMPT_SHA256 = "cac647602bad462315c1eb3b284216e63023c0fcb0aa45c7fd6e85ce12ee3296"


def test_coding_prompt_byte_identical_to_prior_ds_prompt():
    """Resolving domain='coding' returns the existing DS builder-prompt text, byte-identical."""
    text = domain_prompt("coding")
    assert text, "coding domain must resolve to a non-empty prompt"
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CODING_PROMPT_SHA256


def test_coding_domain_object_resolves_prompt_by_domain():
    """The coding prompt lives on the CodingDomain object (D-domain-object-encapsulation).

    DS holds no prompt anymore — it delegates to CodingDomain.run(), whose prompts.system
    resolves from the domain-prompt store by name. This is the coding prompt's one home; DS
    is a thin consumer of it.
    """
    from unseen_university.devices.inference.domains.coding import CodingDomain
    assert CodingDomain().prompts.system == domain_prompt("coding")


def test_unknown_domain_resolves_empty():
    """An unknown / generalist ('') domain resolves to '' — caller keeps its default."""
    assert domain_prompt("no-such-domain") == ""
    assert domain_prompt("") == ""


def test_resolution_is_data_driven_second_domain_independent():
    """A second domain entry resolves independently — the seam is data, not code.

    Uses the `table` injection so no NEW real domain prompt is added (out of scope):
    two domains in one map resolve to their own text with zero selector change.
    """
    table = {"coding": "CODE-PROMPT", "prose": "PROSE-PROMPT"}
    assert domain_prompt("coding", table=table) == "CODE-PROMPT"
    assert domain_prompt("prose", table=table) == "PROSE-PROMPT"
    assert domain_prompt("math", table=table) == ""
