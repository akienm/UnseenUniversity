"""Tests for devices.granny.orchestrator — GrannyPatternOrchestrator."""

from __future__ import annotations

from devices.granny.orchestrator import FactoryTask, GrannyPatternOrchestrator


def _make_orch(members=None, posts=None):
    if posts is None:
        posts = []
    if members is None:
        members = {
            "librarian": "comms://librarian.factory",
            "scraps": "comms://scraps.factory",
        }
    return (
        GrannyPatternOrchestrator(
            factory_id="test-factory",
            owner_id="comms://akien/",
            member_addresses=members,
            channel_post_fn=lambda addr, msg: posts.append((addr, msg)),
        ),
        posts,
    )


# ── receive_goal ─────────────────────────────────────────────────────────────


class TestReceiveGoal:
    def test_posts_goal_received_to_owner(self):
        orch, posts = _make_orch()
        orch.receive_goal("summarise all research papers from last week")
        assert any("FACTORY_GOAL_RECEIVED" in msg for _, msg in posts)
        assert any(addr == "comms://akien/" for addr, _ in posts)

    def test_goal_truncated_in_post(self):
        orch, posts = _make_orch()
        long_goal = "x" * 200
        orch.receive_goal(long_goal)
        msg = next(msg for _, msg in posts if "FACTORY_GOAL_RECEIVED" in msg)
        # message contains truncated goal (≤120 chars in the goal portion)
        assert len(msg) < 300


# ── route_task ───────────────────────────────────────────────────────────────


class TestRouteTask:
    def test_explicit_preference_routes_to_preferred_agent(self):
        orch, posts = _make_orch()
        task = FactoryTask(
            task_id="T-1",
            description="find something",
            preferred_agent_type="librarian",
        )
        result = orch.route_task(task)
        assert result.routed is True
        assert result.agent_type == "librarian"
        assert result.reason == "explicit_preference"

    def test_explicit_preference_unregistered_falls_through(self):
        orch, posts = _make_orch()
        task = FactoryTask(
            task_id="T-2",
            description="embed this doc",
            preferred_agent_type="nonexistent",
        )
        result = orch.route_task(task)
        assert result.routed is True
        # Falls through to keyword or first-available — not nonexistent
        assert result.agent_type in ("librarian", "scraps")

    def test_keyword_match_routes_by_description(self):
        orch, posts = _make_orch()
        task = FactoryTask(
            task_id="T-3",
            description="scraps needs to embed this document",
            preferred_agent_type=None,
        )
        result = orch.route_task(task)
        assert result.routed is True
        assert result.agent_type == "scraps"
        assert result.reason == "keyword_match"

    def test_keyword_match_on_hyphenated_agent_type(self):
        orch, posts = _make_orch(
            members={"custom-intake": "comms://custom-intake.factory"}
        )
        task = FactoryTask(
            task_id="T-4",
            description="custom data from intake pipeline",
            preferred_agent_type=None,
        )
        result = orch.route_task(task)
        assert result.routed is True
        assert result.agent_type == "custom-intake"

    def test_first_available_when_no_keyword_match(self):
        orch, posts = _make_orch()
        task = FactoryTask(
            task_id="T-5",
            description="do something completely unrelated",
            preferred_agent_type=None,
        )
        result = orch.route_task(task)
        assert result.routed is True
        assert result.reason == "first_available"

    def test_routed_task_posts_factory_task_to_member(self):
        orch, posts = _make_orch()
        task = FactoryTask(
            task_id="T-6",
            description="find this",
            preferred_agent_type="librarian",
        )
        orch.route_task(task)
        member_posts = [(addr, msg) for addr, msg in posts if "FACTORY_TASK" in msg]
        assert len(member_posts) == 1
        addr, msg = member_posts[0]
        assert addr == "comms://librarian.factory"
        assert "T-6" in msg

    def test_no_members_escalates_to_owner(self):
        orch, posts = _make_orch(members={})
        task = FactoryTask(task_id="T-7", description="anything")
        result = orch.route_task(task)
        assert result.routed is False
        assert result.reason == "no_members"
        assert any("FACTORY_ESCALATE" in msg for _, msg in posts)
        assert any(addr == "comms://akien/" for addr, _ in posts)


# ── escalate ────────────────────────────────────────────────────────────────


class TestEscalate:
    def test_escalate_posts_factory_escalate_to_owner(self):
        orch, posts = _make_orch()
        orch.escalate("all members unresponsive")
        assert any("FACTORY_ESCALATE" in msg for _, msg in posts)
        assert any(addr == "comms://akien/" for addr, _ in posts)

    def test_escalate_includes_reason_in_message(self):
        orch, posts = _make_orch()
        orch.escalate("budget exceeded")
        msg = next(msg for _, msg in posts if "FACTORY_ESCALATE" in msg)
        assert "budget exceeded" in msg

    def test_escalate_includes_factory_id(self):
        orch, posts = _make_orch()
        orch.escalate("timeout")
        msg = next(msg for _, msg in posts if "FACTORY_ESCALATE" in msg)
        assert "test-factory" in msg


# ── health_summary ───────────────────────────────────────────────────────────


class TestHealthSummary:
    def test_all_healthy_returns_healthy_overall(self):
        orch, _ = _make_orch()
        summary = orch.health_summary({"librarian": "healthy", "scraps": "healthy"})
        assert summary["overall"] == "healthy"

    def test_unknown_treated_as_healthy(self):
        orch, _ = _make_orch()
        summary = orch.health_summary({"librarian": "unknown", "scraps": "healthy"})
        assert summary["overall"] == "healthy"

    def test_degraded_member_makes_overall_degraded(self):
        orch, _ = _make_orch()
        summary = orch.health_summary({"librarian": "degraded", "scraps": "healthy"})
        assert summary["overall"] == "degraded"

    def test_health_summary_includes_factory_id_and_members(self):
        orch, _ = _make_orch()
        summary = orch.health_summary({"librarian": "healthy"})
        assert summary["factory_id"] == "test-factory"
        assert "members" in summary
        assert "checked_at" in summary
