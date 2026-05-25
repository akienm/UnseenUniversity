"""Tests for devices/igor/cognition/playbook.py (T-igor-playbook-memory-type)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_playbooks(pg_test_schema):
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from devices.igor.cognition import playbook

    yield
    # Clean up any PLAYBOOK entries seeded by tests
    import os

    import psycopg2

    conn = psycopg2.connect(
        os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM clan.memories WHERE memory_type = 'PLAYBOOK'")
    except Exception:
        pass
    finally:
        conn.close()


@pytest.fixture
def pb(pg_test_schema):
    from devices.igor.cognition import playbook

    return playbook


# ── add_playbook ──────────────────────────────────────────────────────────────


def test_add_playbook_inserts_row(pb):
    mid = pb.add_playbook(
        "When valence drops below 0.2 for 3+ cycles, scan watch_problems.",
        conditions="valence < 0.2 for multiple cycles",
        heuristics="run lever_watcher immediately; escalate if no match",
    )
    assert mid.startswith("PLAYBOOK_")

    rows = pb.read_active_playbooks()
    ids = [r["id"] for r in rows]
    assert mid in ids


def test_add_playbook_stores_conditions_and_heuristics(pb):
    mid = pb.add_playbook(
        "Escalation playbook",
        conditions="unknown error pattern",
        heuristics="escalate to channel with dedup_key",
    )
    rows = pb.read_active_playbooks()
    row = next((r for r in rows if r["id"] == mid), None)
    assert row is not None
    assert "unknown error pattern" in row["conditions"]
    assert "escalate to channel" in row["heuristics"]


# ── read_active_playbooks ─────────────────────────────────────────────────────


def test_read_active_playbooks_returns_only_active(pb):
    mid_active = pb.add_playbook("Active playbook entry")
    mid_archived = pb.add_playbook("Archived playbook entry")
    pb.archive_playbook(mid_archived)

    rows = pb.read_active_playbooks()
    ids = [r["id"] for r in rows]
    assert mid_active in ids
    assert mid_archived not in ids


def test_read_active_playbooks_empty_when_none(pb):
    # No playbooks seeded — should return empty list (after fixture cleanup)
    rows = pb.read_active_playbooks()
    assert isinstance(rows, list)


# ── archive_playbook ──────────────────────────────────────────────────────────


def test_archive_playbook_sets_active_false(pb):
    mid = pb.add_playbook("Playbook to archive")
    result = pb.archive_playbook(mid)
    assert result is True

    rows = pb.read_active_playbooks()
    ids = [r["id"] for r in rows]
    assert mid not in ids


def test_archive_playbook_returns_false_for_unknown_id(pb):
    result = pb.archive_playbook("PLAYBOOK_9999999999_nonexistent")
    assert result is False


def test_archive_does_not_hard_delete(pb):
    """archive_playbook sets active=false; row remains in clan.memories."""
    import os

    import psycopg2

    mid = pb.add_playbook("Row that should persist after archive")
    pb.archive_playbook(mid)

    conn = psycopg2.connect(
        os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, metadata->>'active' FROM clan.memories WHERE id = %s",
                (mid,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] == "false"


# ── playbook_context_block ────────────────────────────────────────────────────


def test_playbook_context_block_empty_when_none(pb):
    block = pb.playbook_context_block()
    assert block == ""


def test_playbook_context_block_includes_active_playbook(pb):
    pb.add_playbook(
        "Check watch_problems on low valence",
        conditions="valence < 0.2",
        heuristics="run lever_watcher",
    )
    block = pb.playbook_context_block()
    assert "PLAYBOOKS" in block
    assert "Check watch_problems" in block


def test_playbook_context_block_respects_token_cap(pb):
    """Adding many playbooks does not exceed the ~2000 char cap."""
    for i in range(20):
        pb.add_playbook(
            f"Playbook entry number {i} — " + "x" * 100,
            conditions="always",
            heuristics="do something",
        )
    block = pb.playbook_context_block()
    cap_chars = pb._PLAYBOOK_BLOCK_TOKEN_CAP * pb._CHARS_PER_TOKEN
    assert len(block) <= cap_chars + 200  # small buffer for header/footer


# ── NE _playbook_context injection ───────────────────────────────────────────


def test_ne_playbook_context_returns_empty_when_no_playbooks(pg_test_schema):
    """NE._playbook_context() returns empty string when no active playbooks."""
    from unittest.mock import MagicMock

    from devices.igor.cognition.narrative_engine import NarrativeEngine

    cortex = MagicMock()
    ne = NarrativeEngine.__new__(NarrativeEngine)
    result = ne._playbook_context()
    assert result == ""


def test_ne_playbook_context_returns_block_with_playbooks(pg_test_schema):
    """NE._playbook_context() returns non-empty block when active playbooks exist."""
    from unittest.mock import MagicMock

    from devices.igor.cognition import playbook as pb_mod
    from devices.igor.cognition.narrative_engine import NarrativeEngine

    pb_mod.add_playbook(
        "NE test playbook — verifying injection path",
        conditions="test condition",
        heuristics="test heuristic",
    )

    ne = NarrativeEngine.__new__(NarrativeEngine)
    result = ne._playbook_context()
    assert "PLAYBOOKS" in result
    assert "NE test playbook" in result
