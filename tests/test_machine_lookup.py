"""
test_machine_lookup.py — T-machine-names-registry.

Tests the machine_lookup tool surface that resolves machine names through
the canonical registry (machines.json + machine_manager.resolve_alias)
instead of pattern-matching them as hostnames.

Igor drafted this ticket on 2026-04-12 after observing CC abbreviating
'akienyoga9i' to 'yoga9i' and then treating 'yoga9i' as if it were a
hostname. The infrastructure (registry, resolve_alias, populated aliases)
already existed; this sprint added the tool surface so conversational
agents can call it before acting on a name.

Tests cover:
  - machine_lookup resolves canonical hostnames
  - machine_lookup resolves aliases (yoga9i, the dell, pi)
  - machine_lookup is case-insensitive
  - machine_lookup returns clear error for unregistered names — never
    pattern-matches or guesses
  - machine_lookup returns error for empty/whitespace input
  - machine_list_all returns every machine in the registry
  - both functions registered as tools in the registry
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── machine_lookup ───────────────────────────────────────────────────────────


def test_machine_lookup_resolves_canonical_hostname():
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="akienyoga9i")
    assert "hostname: akienyoga9i" in out
    assert "10.0.0.90" in out
    assert "[NOT REGISTERED]" not in out
    assert "[ERROR]" not in out


def test_machine_lookup_resolves_short_alias_yoga9i():
    """The exact case Igor flagged: 'yoga9i' should resolve to akienyoga9i,
    not be treated as a hostname."""
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="yoga9i")
    assert "hostname: akienyoga9i" in out


def test_machine_lookup_resolves_phrase_alias():
    """Multi-word aliases like 'the dell' work."""
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="the dell")
    assert "hostname: akiendell" in out


def test_machine_lookup_resolves_pi_alias():
    """Three-machine alias coverage check — 'pi' resolves to akienpi."""
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="pi")
    assert "hostname: akienpi" in out


def test_machine_lookup_case_insensitive():
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    assert "hostname: akienyoga9i" in machine_lookup(name="YOGA9I")
    assert "hostname: akienyoga9i" in machine_lookup(name="Yoga9i")
    assert "hostname: akiendell" in machine_lookup(name="The Dell")


def test_machine_lookup_unregistered_returns_clear_error():
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="totally-fake-machine")
    assert "[NOT REGISTERED]" in out
    assert "totally-fake-machine" in out
    # And it tells the caller what to do
    assert "machine_list_all" in out


def test_machine_lookup_empty_input_returns_error():
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    assert "[ERROR]" in machine_lookup(name="")
    assert "[ERROR]" in machine_lookup(name="   ")
    assert "[ERROR]" in machine_lookup(name=None)


def test_machine_lookup_includes_aliases_in_output():
    """The lookup result should show what other aliases the resolved
    machine has, so the caller can learn the canonical and use it next time."""
    from wild_igor.igor.tools.machine_lookup import machine_lookup

    out = machine_lookup(name="yoga9i")
    assert "aliases:" in out
    # Should list the registered aliases for akienyoga9i
    assert "yoga9i" in out


# ── machine_list_all ─────────────────────────────────────────────────────────


def test_machine_list_all_returns_all_registered_machines():
    from wild_igor.igor.tools.machine_lookup import machine_list_all

    out = machine_list_all()
    # Five known machines as of 2026-04-13
    for hostname in (
        "akiendelllinux",
        "akiendell",
        "akienyoga9i",
        "akienyogai7",
    ):
        assert hostname in out, f"missing {hostname} from machine_list_all output"


def test_machine_list_all_shows_aliases_per_machine():
    from wild_igor.igor.tools.machine_lookup import machine_list_all

    out = machine_list_all()
    assert "aliases:" in out
    # Specific aliases should appear
    assert "yoga9i" in out
    assert "the dell" in out


# ── Tool registration ───────────────────────────────────────────────────────


def test_machine_lookup_registered_in_tool_registry():
    # Ensure the module is loaded so its registrations fire
    import wild_igor.igor.tools.machine_lookup  # noqa: F401
    from wild_igor.igor.tools.registry import registry

    assert registry.get("machine_lookup") is not None
    assert registry.get("machine_list_all") is not None


def test_machine_lookup_tool_has_required_param():
    import wild_igor.igor.tools.machine_lookup  # noqa: F401
    from wild_igor.igor.tools.registry import registry

    tool = registry.get("machine_lookup")
    assert tool is not None
    params = tool.parameters
    assert "name" in params.get("properties", {})
    assert "name" in params.get("required", [])
