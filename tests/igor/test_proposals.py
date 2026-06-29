"""Tests for instance.proposals queue (T-igor-proposals-queue)."""

import pytest


@pytest.fixture
def proposals(pg_test_schema):
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from unseen_university.devices.igor.cognition import proposals

    return proposals


def test_add_proposal_inserts_row(proposals):
    pid = proposals.add_proposal(
        kind="habit", content="do the thing", source_module="test"
    )
    assert pid > 0


def test_read_pending_returns_only_pending(proposals):
    proposals.add_proposal(kind="habit", content="pending one", source_module="test")
    pending = proposals.read_pending()
    assert any(p["content"] == "pending one" for p in pending)


def test_commit_proposal_excludes_from_pending(proposals):
    pid = proposals.add_proposal(
        kind="watch_q", content="watch this", source_module="test"
    )
    proposals.commit_proposal(pid, memory_id=None)
    pending = proposals.read_pending()
    assert not any(p["id"] == pid for p in pending)


def test_commit_proposal_stores_memory_id(proposals):
    import psycopg2, os

    pid = proposals.add_proposal(
        kind="habit", content="to commit", source_module="test"
    )
    proposals.commit_proposal(pid, memory_id=42)
    conn = psycopg2.connect(
        os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
        )
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT committed_memory_id, status FROM instance.proposals WHERE id = %s",
            (pid,),
        )
        row = cur.fetchone()
    conn.close()
    assert row[0] == 42
    assert row[1] == "committed"


def test_reject_proposal_excludes_from_pending(proposals):
    pid = proposals.add_proposal(
        kind="archive_action", content="to reject", source_module="test"
    )
    proposals.reject_proposal(pid, reason="not relevant")
    pending = proposals.read_pending()
    assert not any(p["id"] == pid for p in pending)


def test_reject_proposal_stores_reason(proposals):
    import psycopg2, os

    pid = proposals.add_proposal(
        kind="archive_action", content="reject me", source_module="test"
    )
    proposals.reject_proposal(pid, reason="stale pattern")
    conn = psycopg2.connect(
        os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
        )
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rejected_reason, status FROM instance.proposals WHERE id = %s",
            (pid,),
        )
        row = cur.fetchone()
    conn.close()
    assert row[0] == "stale pattern"
    assert row[1] == "rejected"


def test_dedup_increments_occurrence_count(proposals):
    import uuid

    content = f"recurring pattern alpha {uuid.uuid4()}"
    pid1 = proposals.add_proposal(kind="habit", content=content, source_module="test")
    pid2 = proposals.add_proposal(kind="habit", content=content, source_module="test")
    assert pid1 == pid2  # same row returned
    pending = proposals.read_pending()
    row = next(p for p in pending if p["id"] == pid1)
    assert row["occurrence_count"] == 2


def test_different_content_creates_separate_rows(proposals):
    pid1 = proposals.add_proposal(
        kind="habit", content="pattern A unique xyz", source_module="test"
    )
    pid2 = proposals.add_proposal(
        kind="habit", content="pattern B unique xyz", source_module="test"
    )
    assert pid1 != pid2


def test_read_pending_includes_occurrence_count(proposals):
    proposals.add_proposal(
        kind="playbook", content="play this out", source_module="test"
    )
    pending = proposals.read_pending()
    assert all("occurrence_count" in p for p in pending)
