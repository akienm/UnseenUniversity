"""
test_pr_accretion.py — T-pr-accretion.

Tests the per-turn online accretion module that writes relationship-level
observations into a persistent-relationship's facia subtree. Each test
creates accretions against PR_AKIEN and queries them back via
pr_recent_accretions.

Cleanup: each test deletes any PRA_* memories it created so the cortex
doesn't accumulate test trash across runs.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _delete_test_accretions(**_):
    """Wipe accretions created in this test session — those whose metadata
    marks them as test entries."""
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memories WHERE id LIKE 'PRA_%' "
        "AND metadata @> '{\"test_marker\": true}'::jsonb"
    )
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_after_each():
    """Clean up test markers before AND after each test to ensure isolation."""
    _delete_test_accretions()
    yield
    _delete_test_accretions()


# ── pr_accrete (generic) ─────────────────────────────────────────────────────


def test_pr_accrete_creates_episodic_with_facia_link():
    from devices.igor.tools import pr_accretion as _pra

    mem_id = _pra.pr_accrete(
        facia_id="PR_AKIEN",
        content_type="exchange",
        narrative="test exchange",
        metadata={"test_marker": True, "user_text": "hi"},
    )
    assert mem_id is not None
    assert mem_id.startswith("PRA_")

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    matching = [r for r in rows if r["id"] == mem_id]
    assert len(matching) == 1
    row = matching[0]
    assert row["metadata"]["pr_facia_id"] == "PR_AKIEN"
    assert row["metadata"]["content_type"] == "exchange"
    assert "accreted_at" in row["metadata"]
    assert row["narrative"] == "test exchange"


def test_pr_accrete_failure_returns_none_not_raise():
    """Passing junk should produce None, never an exception."""
    from devices.igor.tools import pr_accretion as _pra

    # Empty facia_id is still accepted by the generic entry — the validation
    # is at the caller level (the dispatcher only calls when frame applies).
    # What we want to verify: even with bad input, no exception escapes.
    try:
        result = _pra.pr_accrete(
            facia_id="",
            content_type="garbage",
            narrative="",
            metadata=None,
        )
    except Exception:
        pytest.fail("pr_accrete should never raise — must catch internally")
    # Either None or a memory id — both are acceptable; the contract is
    # only that it doesn't crash.
    assert result is None or result.startswith("PRA_")


# ── pr_accrete_exchange ──────────────────────────────────────────────────────


def test_pr_accrete_exchange_stores_both_sides_verbatim():
    from devices.igor.tools import pr_accretion as _pra

    user_text = (
        "Read this now: /home/akien/TheIgorsProject/akien/Readings/"
        "20260412.ClaudeBecameABiomimeticEngineer.txt"
    )
    igor_reply = (
        "I'll read it. The biomimicry conversation looks dense — let me "
        "see what's in there before I share thoughts."
    )

    mem_id = _pra.pr_accrete_exchange(
        facia_id="PR_AKIEN",
        user_text=user_text,
        igor_reply=igor_reply,
        thread_id="web:shared",
        turn_id="abcd1234",
        author="akien",
    )
    # Mark for cleanup
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memories SET metadata = metadata || '{\"test_marker\": true}'::jsonb "
        "WHERE id = %s",
        (mem_id,),
    )
    conn.close()

    assert mem_id is not None
    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    row = next(r for r in rows if r["id"] == mem_id)

    meta = row["metadata"]
    assert meta["content_type"] == "exchange"
    assert meta["pr_facia_id"] == "PR_AKIEN"
    # Verbatim preservation — full path must be in metadata, not truncated
    assert meta["user_text"] == user_text
    assert "20260412.ClaudeBecameABiomimeticEngineer.txt" in meta["user_text"]
    assert meta["igor_reply"] == igor_reply
    assert meta["thread_id"] == "web:shared"
    assert meta["turn_id"] == "abcd1234"
    assert meta["author"] == "akien"
    assert meta["user_char_len"] == len(user_text)
    assert meta["igor_char_len"] == len(igor_reply)


def test_pr_accrete_exchange_narrative_is_truncated_summary():
    from devices.igor.tools import pr_accretion as _pra

    # Realistic-looking long text — repeated raw chars (e.g. 'x' * 5000) trip
    # Igor's credential scrubber. A repeated sentence stays past the filter.
    sentence_u = (
        "The biomimicry framing keeps clicking — every fix today has been "
        "dumber and more correct than the framing it replaced. "
    )
    sentence_i = (
        "Yeah, the loop selects the right thing as long as you seed the "
        "right activations and trust the competition to resolve it. "
    )
    long_user = (sentence_u * 40)[:5000]
    long_igor = (sentence_i * 40)[:5000]
    mem_id = _pra.pr_accrete_exchange(
        facia_id="PR_AKIEN",
        user_text=long_user,
        igor_reply=long_igor,
    )
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memories SET metadata = metadata || '{\"test_marker\": true}'::jsonb "
        "WHERE id = %s",
        (mem_id,),
    )
    conn.close()

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    row = next(r for r in rows if r["id"] == mem_id)
    # Narrative is ≤ ~240 chars (the gist)
    assert len(row["narrative"]) <= 260
    # Verbatim is full length in metadata — assert against the actual
    # input length (the slice may not land cleanly on 5000 due to the
    # source sentence's char count).
    assert len(row["metadata"]["user_text"]) == len(long_user)
    assert len(row["metadata"]["igor_reply"]) == len(long_igor)
    assert len(long_user) > 1000
    assert len(long_igor) > 1000


# ── markers ──────────────────────────────────────────────────────────────────


def test_detect_marker_finds_explicit_phrases():
    from devices.igor.tools.pr_accretion import detect_marker

    assert detect_marker("Remember this: never delete the live db") == "remember this"
    assert detect_marker("don't forget the certificate path") == "don't forget"
    assert detect_marker("This is important: the cap is 40K") == "this is important"
    assert detect_marker("hey can you do this for me") is None
    assert detect_marker("") is None
    assert detect_marker(None) is None


def test_pr_accrete_marker_creates_marker_memory():
    from devices.igor.tools import pr_accretion as _pra

    mem_id = _pra.pr_accrete_marker(
        facia_id="PR_AKIEN",
        marker_text="Remember this: never delete the live db",
        why="matched 'remember this'",
        thread_id="web:shared",
        turn_id="markerturn",
    )
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memories SET metadata = metadata || '{\"test_marker\": true}'::jsonb "
        "WHERE id = %s",
        (mem_id,),
    )
    conn.close()

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    row = next(r for r in rows if r["id"] == mem_id)
    assert row["metadata"]["content_type"] == "marker"
    assert "remember this" in row["metadata"]["marker_text"].lower()
    assert "remember this" in row["metadata"]["why"]
    assert row["narrative"].startswith("[marker]")


# ── commitments ──────────────────────────────────────────────────────────────


def test_pr_accrete_commitment_links_to_goal():
    from devices.igor.tools import pr_accretion as _pra

    mem_id = _pra.pr_accrete_commitment(
        facia_id="PR_AKIEN",
        commitment_text="let me look at the ticket list",
        goal_id="GOAL_TEST_COMMIT_001",
        thread_id="web:shared",
        turn_id="commitmentturn",
    )
    import psycopg2

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memories SET metadata = metadata || '{\"test_marker\": true}'::jsonb "
        "WHERE id = %s",
        (mem_id,),
    )
    conn.close()

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    row = next(r for r in rows if r["id"] == mem_id)
    assert row["metadata"]["content_type"] == "commitment"
    assert row["metadata"]["goal_id"] == "GOAL_TEST_COMMIT_001"
    assert row["metadata"]["commitment_text"] == "let me look at the ticket list"


# ── recent retrieval ordering ────────────────────────────────────────────────


def test_pr_recent_accretions_orders_newest_first():
    from devices.igor.tools import pr_accretion as _pra

    # Use a sentinel facia_id so Igor's concurrent PR_AKIEN writes don't
    # push our 3 test rows out of the limit=10 window.
    _SENTINEL_FACIA = "PR_TEST_ORDERING_SENTINEL"

    ids = []
    for n in range(3):
        mid = _pra.pr_accrete(
            facia_id=_SENTINEL_FACIA,
            content_type="exchange",
            narrative=f"ordered test {n}",
            metadata={"test_marker": True, "ord": n},
        )
        ids.append(mid)
        time.sleep(0.001)

    rows = _pra.pr_recent_accretions(_SENTINEL_FACIA, limit=10)
    # The first 3 rows should be our test accretions in newest-first order.
    test_rows = [r for r in rows if r["id"] in ids]
    assert len(test_rows) == 3
    # ord=2 (most recent) first, ord=0 (oldest) last
    assert test_rows[0]["metadata"]["ord"] == 2
    assert test_rows[2]["metadata"]["ord"] == 0
