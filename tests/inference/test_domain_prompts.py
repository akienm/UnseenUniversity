"""
Tests for T-inference-domain-prompt: the system prompt is DATA keyed by domain.

The router routes BOTH model and prompt by domain (Intention-Based Development).
The DS 'coding' builder prompt moved VERBATIM into the domain-prompt store; DS
resolves it by domain.

The hash pin below started life guarding that MOVE ("byte-identical to the prior DS
prompt"). The move is long done, and the prompt has since changed on purpose — so the
pin now guards something narrower and more useful: that nobody edits the coding prompt
WITHOUT NOTICING. It is a drift alarm, not a statement about any historical text
(T-repin-coding-prompt-sha256).

A hash alarm cannot say which edits are dangerous, only that one happened. The edit
that actually hurt is pinned separately, by property, in
`test_the_coding_prompt_injects_no_absolute_paths`.
"""

from __future__ import annotations

import hashlib
import re

from unseen_university.devices.inference.domains.domain_prompts import domain_prompt

# Anchor: sha256 of the coding prompt as it is INTENDED to stand today — the post-124553ee
# text, which stripped the six hardcoded ~/dev/src/UnseenUniversity paths (see below), and
# which commit ea74a6a0 then MOVED without changing a byte. Re-pin deliberately, never to
# silence a red: a mismatch means the prompt changed, and the only question worth asking is
# whether that change was meant.
_CODING_PROMPT_SHA256 = "0e044ff70e1d4c2c963619c0d002b0164be5ea5b4ef01a0ca169263e027decd7"

#: The exact shape of the bug 124553ee fixed. The prompt is allowed to SAY `/home/...` inside
#: its own prohibition ("NEVER write an absolute path (no `/home/...`)"); what it may never do
#: is hand the model a real absolute path to follow.
_ABSOLUTE_PATH = re.compile(r"~/dev/src/UnseenUniversity|/home/akien")


def test_the_coding_prompt_matches_its_pinned_hash():
    """The coding prompt has not drifted since it was last deliberately pinned."""
    text = domain_prompt("coding")
    assert text, "coding domain must resolve to a non-empty prompt"
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CODING_PROMPT_SHA256, (
        "the coding prompt changed. If the change was intended, re-pin _CODING_PROMPT_SHA256 "
        "to sha256(domain_prompt('coding')) AND confirm the property tests below still hold. "
        "If it was not intended, revert the prompt — this alarm is the only thing standing "
        "between a stray edit and every DickSimnel run that reads it."
    )


def test_the_coding_prompt_injects_no_absolute_paths():
    """DickSimnel follows the prompt LITERALLY, so an absolute path in it escapes the sandbox.

    Root cause of the first harvest batch's contamination (124553ee): the prompt hardcoded
    `~/dev/src/UnseenUniversity` in six places, so devstral prepended the live-repo path to
    relative signature-map paths and wandered the live tree instead of its throwaway clone.
    The working directory is already set by the loop; every path in the prompt must be
    relative to it.

    This is the assertion the hash pin cannot make. Measured to discriminate: the pre-fix
    text (124553ee^) trips it 6 times; the current text, 0.
    """
    hits = _ABSOLUTE_PATH.findall(domain_prompt("coding"))
    assert not hits, (
        f"the coding prompt hands DickSimnel {len(hits)} absolute path(s) to follow: "
        f"{sorted(set(hits))}. Its cwd is already the repo root — use relative paths only, "
        f"or the builder will wander outside whatever tree it was pointed at."
    )


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
