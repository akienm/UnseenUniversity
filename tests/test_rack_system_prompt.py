"""
Tests for the default rack system prompt injected into every agent manifest.

Completion criteria (T-rack-agent-system-prompt):
  1. A newly announced agent receives a manifest with a non-empty system_prompt
     containing rack orientation text.
  2. An agent-type profile can override individual sections.
  3. An agent-type profile can extend with additional sections.
  4. Existing agents (no system_prompt_sections in profile) are unaffected
     by the change — they still receive a manifest; system_prompt is non-empty.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from unseen_university.announce.broker import AnnounceBroker, ManifestAssembler
from unseen_university.announce.envelope import IdentityEnvelope

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_envelope(agent_id: str = "igor") -> IdentityEnvelope:
    return IdentityEnvelope(
        agent_id=agent_id,
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
        surfaces=["console"],
    )


def _make_broker(profiles_dir: Path) -> AnnounceBroker:
    return AnnounceBroker(profiles_dir=profiles_dir, registry=None, devices={})


def _write_profile(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")


# ── 1. Default system_prompt is non-empty ─────────────────────────────────────


def test_manifest_system_prompt_non_empty(tmp_path: Path) -> None:
    """An announced agent receives a manifest with non-empty system_prompt."""
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("igor"))
    assert manifest.system_prompt, "system_prompt must not be empty"


def test_manifest_system_prompt_contains_rack_orientation(tmp_path: Path) -> None:
    """system_prompt contains 'rack' orientation text from base.yaml."""
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("igor"))
    prompt = manifest.system_prompt.lower()
    assert "rack" in prompt
    assert "comms://" in manifest.system_prompt


# ── 2. Profile override of a section ─────────────────────────────────────────


def test_profile_system_prompt_override(tmp_path: Path) -> None:
    """A profile that sets system_prompt_sections.rack replaces that section."""
    _write_profile(
        tmp_path,
        "testbot",
        """
profile_version: "1.0"
agent_type: testbot
inherits: []
system_prompt_sections:
  rack: "You are a specialized test bot on the rack."
""",
    )
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("testbot"))
    assert "specialized test bot" in manifest.system_prompt
    # Other base sections should still be present
    assert (
        "recall" in manifest.system_prompt.lower()
        or "comms://" in manifest.system_prompt
    )


def test_profile_override_replaces_only_targeted_section(tmp_path: Path) -> None:
    """Overriding one section leaves other base sections intact."""
    _write_profile(
        tmp_path,
        "testbot",
        """
profile_version: "1.0"
agent_type: testbot
inherits: []
system_prompt_sections:
  rack: "Custom rack description."
""",
    )
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("testbot"))
    assert "Custom rack description." in manifest.system_prompt
    # recall section from base should still be present
    assert "recall" in manifest.system_prompt.lower()


# ── 3. Profile extension with a new section ──────────────────────────────────


def test_profile_system_prompt_extend(tmp_path: Path) -> None:
    """A profile that adds a new section appends it to the assembled prompt."""
    _write_profile(
        tmp_path,
        "testbot",
        """
profile_version: "1.0"
agent_type: testbot
inherits: []
system_prompt_sections:
  my_custom: "This agent specializes in data analysis."
""",
    )
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("testbot"))
    assert "data analysis" in manifest.system_prompt
    # Base sections still present
    assert "rack" in manifest.system_prompt.lower()


# ── 4. Existing agents unaffected ────────────────────────────────────────────


def test_existing_igor_profile_still_resolves(tmp_path: Path) -> None:
    """igor.yaml (no system_prompt_sections) still resolves a valid manifest."""
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("igor"))
    assert manifest.schema_version == "1.0"
    assert manifest.issued_to["agent_id"] == "igor"
    # system_prompt is now non-empty even for igor (gets the base prompt)
    assert manifest.system_prompt


def test_existing_cc_profile_still_resolves(tmp_path: Path) -> None:
    """cc.yaml (no system_prompt_sections) still resolves a valid manifest."""
    shutil.copy(CANONICAL_PROFILES / "cc.yaml", tmp_path / "cc.yaml")
    broker = _make_broker(tmp_path)
    manifest = broker.resolve_announce(_make_envelope("cc"))
    assert manifest.schema_version == "1.0"
    assert manifest.issued_to["agent_id"] == "cc"
    assert manifest.system_prompt


# ── 5. ManifestAssembler unit tests ──────────────────────────────────────────


def _assembler(profile: dict, base_sections: dict | None = None) -> ManifestAssembler:
    return ManifestAssembler(
        profile=profile,
        online_devices=[],
        live_devices={},
        base_system_sections=base_sections,
    )


def test_build_system_prompt_empty_when_no_sections() -> None:
    assembler = _assembler({"profile_version": "1.0", "agent_type": "x"})
    assert assembler.build_system_prompt() == ""


def test_build_system_prompt_uses_base_sections() -> None:
    base = {"rack": "This is the rack.", "recall": "Use recall."}
    assembler = _assembler({"profile_version": "1.0", "agent_type": "x"}, base)
    prompt = assembler.build_system_prompt()
    assert "This is the rack." in prompt
    assert "Use recall." in prompt


def test_build_system_prompt_section_order() -> None:
    base = {
        "questions": "Q section.",
        "rack": "Rack section.",
        "recall": "Recall section.",
    }
    assembler = _assembler({"profile_version": "1.0", "agent_type": "x"}, base)
    prompt = assembler.build_system_prompt()
    rack_pos = prompt.index("Rack section.")
    recall_pos = prompt.index("Recall section.")
    questions_pos = prompt.index("Q section.")
    assert rack_pos < recall_pos < questions_pos


def test_build_system_prompt_profile_overrides_base() -> None:
    base = {"rack": "Base rack.", "recall": "Base recall."}
    profile = {
        "profile_version": "1.0",
        "agent_type": "x",
        "system_prompt_sections": {"rack": "Custom rack."},
    }
    assembler = _assembler(profile, base)
    prompt = assembler.build_system_prompt()
    assert "Custom rack." in prompt
    assert "Base rack." not in prompt
    assert "Base recall." in prompt


def test_build_system_prompt_profile_adds_section() -> None:
    base = {"rack": "The rack."}
    profile = {
        "profile_version": "1.0",
        "agent_type": "x",
        "system_prompt_sections": {"extra": "Extra info."},
    }
    assembler = _assembler(profile, base)
    prompt = assembler.build_system_prompt()
    assert "The rack." in prompt
    assert "Extra info." in prompt
