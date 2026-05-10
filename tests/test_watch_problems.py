"""Tests for T-igor-watch-list: instance.watch_problems API."""

import pytest


@pytest.fixture
def wp(pg_test_schema):
    """Import watch_problems with test schema active."""
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from wild_igor.igor.cognition import watch_problems

    return watch_problems


def test_add_watch_problem_writes_row(wp):
    row_id = wp.add_watch_problem(
        problem="Cannot plan ticket — no description",
        lever_description="Need Affected files + scope",
        watch_condition="ticket description scope affected files",
    )
    assert row_id > 0


def test_read_active_problems_returns_unresolved(wp):
    wp.add_watch_problem(
        problem="NE produced no result",
        watch_condition="twm working memory stuck",
    )
    active = wp.read_active_problems()
    assert any("NE produced no result" in p["problem"] for p in active)


def test_resolved_excluded_from_active(wp):
    row_id = wp.add_watch_problem(
        problem="Resolved test problem",
        watch_condition="resolved condition",
    )
    assert row_id > 0
    wp.resolve_problem(row_id)
    active = wp.read_active_problems()
    assert not any(p["id"] == row_id for p in active)


def test_mark_surfaced_updates_timestamp(wp):
    row_id = wp.add_watch_problem(
        problem="Surfaced test problem",
        watch_condition="some condition",
    )
    assert row_id > 0
    wp.mark_surfaced(row_id)
    active = wp.read_active_problems()
    surfaced = next((p for p in active if p["id"] == row_id), None)
    assert surfaced is not None
    assert surfaced["last_surfaced_at"] is not None


def test_parent_id_tree_structure(wp):
    root_id = wp.add_watch_problem(problem="Root problem", watch_condition="root")
    child_id = wp.add_watch_problem(
        problem="Child sub-problem",
        watch_condition="child",
        parent_id=root_id,
    )
    assert root_id > 0
    assert child_id > 0
    assert child_id != root_id
    active = wp.read_active_problems()
    child = next((p for p in active if p["id"] == child_id), None)
    assert child is not None
    assert child["parent_id"] == root_id


def test_escalate_creates_watch_entry(pg_test_schema):
    """escalate_to_channel() side-effect writes to instance.watch_problems."""
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from unittest.mock import patch
    from wild_igor.igor.cognition import watch_problems

    before = len(watch_problems.read_active_problems())

    with patch("wild_igor.igor.tools.channel_post.post_to_channel"):
        from wild_igor.igor.cognition.escalate import escalate_to_channel

        escalate_to_channel(
            "[NE] stuck — no result",
            dedup_key="ne-empty-test",
            watch_condition="ne empty result stuck",
        )

    after = watch_problems.read_active_problems()
    assert len(after) > before
    assert any("stuck" in p["problem"] for p in after)


def test_lever_watcher_finds_match(wp):
    wp.add_watch_problem(
        problem="Igor stuck on fee schedule ticket",
        watch_condition="fee schedule billing invoice",
    )
    fake_twm = [{"content_csb": "user mentioned fee schedule update for billing"}]
    count = wp.lever_watcher(recent_twm_rows=fake_twm)
    assert count >= 1


def test_lever_watcher_no_match_on_unrelated(wp):
    wp.add_watch_problem(
        problem="Problem about elephants",
        watch_condition="elephant savanna migration",
    )
    fake_twm = [{"content_csb": "user asked about python decorators"}]
    count = wp.lever_watcher(recent_twm_rows=fake_twm)
    assert count == 0


def test_lever_watcher_dedup_24h(wp):
    """Already-surfaced-recently problems are skipped."""
    from datetime import datetime, timezone, timedelta

    row_id = wp.add_watch_problem(
        problem="Recently surfaced problem",
        watch_condition="recent surface test keywords",
    )
    # Mark as surfaced 1 hour ago — within 24h window
    import psycopg2, os

    conn = psycopg2.connect(
        os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE instance.watch_problems SET last_surfaced_at = %s WHERE id = %s",
                (datetime.now(timezone.utc) - timedelta(hours=1), row_id),
            )
    conn.close()

    fake_twm = [{"content_csb": "recent surface test keywords match found here"}]
    count = wp.lever_watcher(recent_twm_rows=fake_twm)
    assert count == 0
