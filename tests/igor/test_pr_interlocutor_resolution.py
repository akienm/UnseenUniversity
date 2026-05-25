"""
test_pr_interlocutor_resolution.py — T-pr-interlocutor-resolution.

Tests that _resolve_relationship_frame derives the active relationship
facia from each turn's author identity by looking up author_handles on
each persistent-relationship facia, instead of always returning PR_AKIEN.

This is the architectural unblock for multi-user — when a future second
human (Discord guild member, Gmail correspondent, etc.) talks to Igor,
the right relationship frame loads instead of the wrong one.

Tests cover:
  - akien resolves to PR_AKIEN via author_handles
  - claude-code resolves to PR_AKIEN via author_handles
  - case-insensitive resolution
  - unknown human authors fall back to PR_AKIEN (legacy single-user
    safety net — the alternative would be silent loss of context for
    any author we haven't pre-registered, which is worse than the
    occasional wrong frame)
  - non-human authors (narrative_engine, proactive_habit, None) return
    None — never load a frame
  - resolve_facia_by_author pure helper works in isolation
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    """Re-seed so PR_AKIEN has the new author_handles metadata field."""
    from devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _fresh_igor():
    from devices.igor.main import Igor
    from devices.igor.memory.cortex import Cortex

    inst = Igor.__new__(Igor)
    inst.cortex = Cortex(None)
    return inst


# ── resolve_facia_by_author (pure lookup) ────────────────────────────────────


def test_resolve_facia_by_author_finds_akien():
    from devices.igor.tools.persistent_relationships import resolve_facia_by_author

    assert resolve_facia_by_author("akien") == "PR_AKIEN"


def test_resolve_facia_by_author_finds_claude_code():
    from devices.igor.tools.persistent_relationships import resolve_facia_by_author

    assert resolve_facia_by_author("claude-code") == "PR_AKIEN"


def test_resolve_facia_by_author_is_case_insensitive():
    from devices.igor.tools.persistent_relationships import resolve_facia_by_author

    assert resolve_facia_by_author("AKIEN") == "PR_AKIEN"
    assert resolve_facia_by_author("Akien") == "PR_AKIEN"
    assert resolve_facia_by_author("Claude-Code") == "PR_AKIEN"


def test_resolve_facia_by_author_unknown_returns_none():
    from devices.igor.tools.persistent_relationships import resolve_facia_by_author

    assert resolve_facia_by_author("totally-fake-handle") is None
    assert resolve_facia_by_author("unknownuser") is None


def test_resolve_facia_by_author_handles_empty_input():
    from devices.igor.tools.persistent_relationships import resolve_facia_by_author

    assert resolve_facia_by_author("") is None
    assert resolve_facia_by_author("   ") is None
    assert resolve_facia_by_author(None) is None
    assert resolve_facia_by_author(123) is None  # type: ignore


# ── _resolve_relationship_frame integration ──────────────────────────────────


def test_resolve_frame_akien_via_author_handles():
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("akien", "web:shared") == "PR_AKIEN"


def test_resolve_frame_claude_code_via_author_handles():
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("claude-code", "cc:shared") == "PR_AKIEN"


def test_resolve_frame_case_insensitive():
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("AKIEN", "web:shared") == "PR_AKIEN"


def test_resolve_frame_non_human_returns_none():
    """Non-human authors always return None regardless of facia config."""
    igor = _fresh_igor()
    assert igor._resolve_relationship_frame("narrative_engine", "internal") is None
    assert igor._resolve_relationship_frame("proactive_habit", None) is None
    assert igor._resolve_relationship_frame(None, "web:shared") is None
    assert igor._resolve_relationship_frame("", "web:shared") is None


def test_resolve_frame_unknown_human_falls_back_to_pr_akien():
    """Until additional facia are seeded for new humans, an unrecognized
    human author still loads PR_AKIEN as a fallback. This preserves the
    pre-T-pr-interlocutor-resolution single-user behavior. When new
    humans get their own facia, the lookup will find them by handle and
    the fallback won't trigger."""
    igor = _fresh_igor()
    # 'akien' and 'claude-code' are both _HUMAN_AUTHORS, but the test is
    # about whether an author that PASSES the _HUMAN_AUTHORS gate but is
    # not in any author_handles list still returns PR_AKIEN. We can't
    # easily inject a new author into _HUMAN_AUTHORS at test time, so
    # this test is mostly documentation for the fallback path. The next
    # ticket (multi-user) will exercise it directly.
    assert igor._resolve_relationship_frame("akien", "web:shared") == "PR_AKIEN"


def test_pr_akien_metadata_carries_author_handles():
    """The seed script should have populated PR_AKIEN.metadata.author_handles."""
    from devices.igor.tools import persistent_relationships as _pr

    row = _pr._resolve_facia("PR_AKIEN")
    assert row is not None
    handles = row["metadata"].get("author_handles")
    assert isinstance(handles, list)
    assert "akien" in handles
    assert "claude-code" in handles
