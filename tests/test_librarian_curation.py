"""Tests for Librarian curation tools — T-librarian-curation-tools."""

from __future__ import annotations

import json
import os
import uuid

import psycopg2
import pytest

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _pg():
    return psycopg2.connect(_PG_URL)


def _unique_id():
    return f"TEST_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _clean_proposals():
    """Delete test proposals between tests."""
    yield
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM instance.proposals WHERE source_module = 'librarian_curation'"
                )
    except Exception:
        pass
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_test_memories():
    """Delete seeded test memories between tests."""
    yield
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM clan.memories WHERE id LIKE 'TEST_%'")
    except Exception:
        pass
    finally:
        conn.close()


def _seed_memory(
    id_: str,
    memory_type: str,
    narrative: str,
    metadata: dict | None = None,
    activation_count: int = 0,
):
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clan.memories "
                    "(id, memory_type, narrative, metadata, activation_count) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s) "
                    "ON CONFLICT (id) DO UPDATE SET narrative=EXCLUDED.narrative, "
                    "    memory_type=EXCLUDED.memory_type, metadata=EXCLUDED.metadata, "
                    "    activation_count=EXCLUDED.activation_count",
                    (
                        id_,
                        memory_type,
                        narrative,
                        json.dumps(metadata or {}),
                        activation_count,
                    ),
                )
    finally:
        conn.close()


# ── Schema registration ───────────────────────────────────────────────────────


def test_librarian_curate_in_schemas():
    from unseen_university.devices.librarian.tools import SCHEMAS

    names = {s["name"] for s in SCHEMAS}
    assert "librarian_curate" in names


# ── Duplicate FACTUAL detection ───────────────────────────────────────────────


def test_finds_duplicate_factual_pair():
    """Two FACTUAL memories with identical narrative → archive_action proposal."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    narrative = (
        "The sky is blue and the grass is green and identical narrative text here"
    )
    id_a = _unique_id()
    id_b = _unique_id()
    _seed_memory(id_a, "FACTUAL", narrative)
    _seed_memory(id_b, "FACTUAL", narrative)

    result = run_curation()

    assert result["findings_count"] >= 1
    assert result["breakdown"]["duplicate_narratives"] >= 1
    assert result["proposals_written"] >= 1


def test_no_duplicate_proposal_for_different_narratives():
    """Two FACTUAL memories with different narratives → no duplicate finding."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    id_a = _unique_id()
    id_b = _unique_id()
    _seed_memory(id_a, "FACTUAL", f"Unique narrative alpha {id_a}")
    _seed_memory(id_b, "FACTUAL", f"Unique narrative beta {id_b}")

    result = run_curation()

    # breakdown for duplicate_narratives should not include our unique pair
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM instance.proposals "
                "WHERE source_module='librarian_curation' AND kind='archive_action'"
            )
            contents = [json.loads(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()

    for c in contents:
        if c.get("reason") == "duplicate_narrative":
            target_ids = c.get("target_ids", [])
            assert id_a not in target_ids or id_b not in target_ids


# ── Stale EPISODIC detection ──────────────────────────────────────────────────


def test_finds_stale_episodic():
    """EPISODIC memory with activation_count=0 and no last_activated_at → stale proposal."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    id_ = _unique_id()
    _seed_memory(
        id_, "EPISODIC", f"Old event that happened long ago {id_}", activation_count=0
    )

    result = run_curation()

    # Count must be ≥1; the seeded memory may not appear in top-50 if many stale exist.
    assert result["breakdown"]["stale_no_activation"] >= 1
    assert result["proposals_written"] >= 1


def test_recently_activated_not_flagged():
    """EPISODIC memory with recent last_activated_at is NOT flagged as stale."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    id_ = _unique_id()
    _seed_memory(id_, "EPISODIC", f"Recent active event {id_}", activation_count=5)
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clan.memories SET last_activated_at = now(), activation_count = 5 "
                    "WHERE id = %s",
                    (id_,),
                )
    finally:
        conn.close()

    run_curation()

    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM instance.proposals "
                "WHERE source_module='librarian_curation' AND kind='archive_action'"
            )
            contents = [json.loads(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()

    stale_ids = [
        tid
        for c in contents
        if c.get("reason") == "stale_no_activation"
        for tid in c.get("target_ids", [])
    ]
    assert id_ not in stale_ids


# ── Duplicate code_ref detection ──────────────────────────────────────────────


def test_finds_duplicate_code_refs():
    """Two PROCEDURAL memories sharing same code_ref → archive_action proposal."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    code_ref = f"wild_igor.igor.tools.test_fn:{_unique_id()}"
    id_a = _unique_id()
    id_b = _unique_id()
    _seed_memory(id_a, "PROCEDURAL", f"Habit A with {code_ref}", {"code_ref": code_ref})
    _seed_memory(id_b, "PROCEDURAL", f"Habit B with {code_ref}", {"code_ref": code_ref})

    result = run_curation()

    assert result["breakdown"]["duplicate_code_refs"] >= 1


# ── Dry run ───────────────────────────────────────────────────────────────────


def test_dry_run_writes_no_proposals():
    """dry_run=True runs analysis but does not write to instance.proposals."""
    from unseen_university.devices.librarian.tools.curation_tools import run_curation

    narrative = (
        "Dry run test narrative that is identical and unique for this test run only"
    )
    id_a = _unique_id()
    id_b = _unique_id()
    _seed_memory(id_a, "FACTUAL", narrative)
    _seed_memory(id_b, "FACTUAL", narrative)

    result = run_curation(dry_run=True)

    assert result["dry_run"] is True
    assert result["proposals_written"] == 0

    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM instance.proposals "
                "WHERE source_module='librarian_curation'"
            )
            count = cur.fetchone()[0]
    finally:
        conn.close()
    assert count == 0


# ── MCP dispatch ──────────────────────────────────────────────────────────────


def test_dispatch_librarian_curate():
    from unseen_university.devices.librarian.tools import dispatch

    result_str = dispatch("librarian_curate", {"dry_run": True})
    assert result_str is not None
    result = json.loads(result_str)
    assert "findings_count" in result
    assert result["dry_run"] is True


def test_dispatch_unknown_returns_none():
    from unseen_university.devices.librarian.tools.curation_tools import dispatch

    assert dispatch("not_a_curation_tool", {}) is None


# ── Focus quality log ─────────────────────────────────────────────────────────


def _seed_quality_log(
    memory_id: str, was_loaded: bool, was_used: bool, contribution: float = 0.0
):
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO instance.focus_quality_log "
                    "(memory_id, was_loaded, was_used, contribution_score) "
                    "VALUES (%s, %s, %s, %s)",
                    (memory_id, was_loaded, was_used, contribution),
                )
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_quality_log():
    yield
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM instance.focus_quality_log WHERE memory_id LIKE 'TEST_%'"
                )
    except Exception:
        pass
    finally:
        conn.close()


def test_quality_log_in_schemas():
    from unseen_university.devices.librarian.tools import SCHEMAS

    names = {s["name"] for s in SCHEMAS}
    assert "librarian_quality_log" in names


def test_read_quality_log_empty():
    """No rows for unknown memory → all zeros, not prune_candidate."""
    from unseen_university.devices.librarian.tools.curation_tools import read_quality_log

    result = read_quality_log("TEST_nonexistent_xyz")
    assert result["loaded_count"] == 0
    assert result["used_count"] == 0
    assert result["prune_candidate"] is False


def test_read_quality_log_loaded_and_used():
    """5 loaded, 4 used → used_rate 80% → not prune_candidate."""
    from unseen_university.devices.librarian.tools.curation_tools import read_quality_log

    mem_id = _unique_id()
    for _ in range(4):
        _seed_quality_log(mem_id, was_loaded=True, was_used=True, contribution=0.8)
    _seed_quality_log(mem_id, was_loaded=True, was_used=False, contribution=0.0)

    result = read_quality_log(mem_id)
    assert result["loaded_count"] == 5
    assert result["used_count"] == 4
    assert result["prune_candidate"] is False


def test_read_quality_log_prune_candidate():
    """5 loaded, 0 used → used_rate 0% → prune_candidate."""
    from unseen_university.devices.librarian.tools.curation_tools import read_quality_log

    mem_id = _unique_id()
    for _ in range(5):
        _seed_quality_log(mem_id, was_loaded=True, was_used=False, contribution=0.0)

    result = read_quality_log(mem_id)
    assert result["loaded_count"] == 5
    assert result["used_count"] == 0
    assert result["prune_candidate"] is True


def test_dispatch_quality_log():
    from unseen_university.devices.librarian.tools import dispatch

    result_str = dispatch("librarian_quality_log", {"memory_id": "TEST_dispatch_check"})
    assert result_str is not None
    result = json.loads(result_str)
    assert "prune_candidate" in result
    assert result["memory_id"] == "TEST_dispatch_check"
