"""Tests for devices/granny/device.py — GrannyWeatherwaxDevice."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devices.granny.device import (
    AuditResult,
    GrannyWeatherwaxDevice,
    RoutingEdge,
    _CC_ESCALATION_TAGS,
    _audit_ticket,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_ticket(**overrides):
    base = {
        "id": "T-test",
        "title": "Test ticket",
        "size": "S",
        "tags": ["Cognition"],
        "description": (
            "Problem description.\n"
            "**Affected files:** foo.py\n"
            "**Scope boundary:** just this\n"
            "**Completion criteria:** run pytest"
        ),
    }
    base.update(overrides)
    return base


@pytest.fixture
def granny():
    with patch.object(GrannyWeatherwaxDevice, "_post_to_channel"):
        g = GrannyWeatherwaxDevice()
        yield g


# ── _audit_ticket ──────────────────────────────────────────────────────────────


def test_audit_valid_ticket_passes():
    result = _audit_ticket(_make_ticket())
    assert result.passed is True
    assert result.reasons == []


def test_audit_missing_title_fails():
    result = _audit_ticket(_make_ticket(title=""))
    assert result.passed is False
    assert any("title" in r for r in result.reasons)


def test_audit_invalid_size_fails():
    result = _audit_ticket(_make_ticket(size="HUGE"))
    assert result.passed is False
    assert any("size" in r for r in result.reasons)


def test_audit_missing_description_section_fails():
    result = _audit_ticket(_make_ticket(description="No sections here"))
    assert result.passed is False
    assert any("Affected files" in r for r in result.reasons)


def test_audit_cc_escalation_tag_sets_escalate():
    # "RackContract" is in _CC_ESCALATION_TAGS — cross-device rack contracts → CC
    result = _audit_ticket(_make_ticket(tags=["RackContract"]))
    assert result.escalate_to_cc is True


def test_audit_normal_tags_no_escalate():
    result = _audit_ticket(_make_ticket(tags=["Cognition", "Memory"]))
    assert result.escalate_to_cc is False


# ── who_am_i / self_test ───────────────────────────────────────────────────────


def test_who_am_i_has_expected_fields(granny):
    info = granny.who_am_i()
    assert info["device_id"] == "granny-weatherwax"
    assert "purpose" in info


def test_self_test_passes(granny):
    result = granny.self_test()
    assert result["passed"] is True


# ── Worker registry ────────────────────────────────────────────────────────────


def test_register_worker_adds_edges(granny):
    granny.register_worker("my-worker", ["Custom"])
    weights = granny.get_edge_weights("Custom")
    assert any(w == "my-worker" for w, _ in weights)


def test_register_worker_dispatch_fn_stored(granny):
    fn = MagicMock(return_value=True)
    granny.register_worker("w1", ["Tag1"], dispatch_fn=fn)
    with granny._lock:
        edges = granny._edges.get("Tag1", [])
    assert any(e.dispatch_fn is fn for e in edges)


# ── intake_ticket ──────────────────────────────────────────────────────────────


def test_intake_valid_ticket_passes(granny):
    result = granny.intake_ticket(_make_ticket())
    assert result.passed is True


def test_intake_invalid_ticket_posts_channel(granny):
    with patch.object(granny, "_post_to_channel") as mock_post:
        granny.intake_ticket(_make_ticket(title=""))
        mock_post.assert_called_once()
        # Message contains the ticket id and failure reason
        msg = mock_post.call_args[0][1]
        assert "T-test" in msg
        assert "title" in msg.lower() or "audit" in msg.lower()


# ── route_ticket ───────────────────────────────────────────────────────────────


def test_route_ticket_cognition_goes_to_igor(granny):
    ticket = _make_ticket(tags=["Cognition"])
    dispatched, worker = granny.route_ticket(ticket)
    assert dispatched is True
    assert worker == "igor"


def test_route_ticket_cc_escalation_tag_escalates(granny):
    with patch.object(granny, "escalate_to_cc") as mock_esc:
        ticket = _make_ticket(tags=["RackContract"])
        dispatched, worker = granny.route_ticket(ticket)
        assert dispatched is False
        assert worker == "escalated_to_cc"
        mock_esc.assert_called_once()


def test_route_ticket_unknown_tag_escalates(granny):
    with patch.object(granny, "escalate_to_cc") as mock_esc:
        ticket = _make_ticket(tags=["UnknownTag999"])
        dispatched, worker = granny.route_ticket(ticket)
        assert dispatched is False
        assert worker == "no_route"
        mock_esc.assert_called_once()


def test_route_ticket_dispatch_fn_called(granny):
    fn = MagicMock(return_value=True)
    granny.register_worker("custom-worker", ["Special"], dispatch_fn=fn)
    ticket = _make_ticket(tags=["Special"])
    dispatched, worker = granny.route_ticket(ticket)
    assert dispatched is True
    assert worker == "custom-worker"
    fn.assert_called_once_with(ticket)


def test_route_ticket_dispatch_fn_failure(granny):
    fn = MagicMock(return_value=False)
    granny.register_worker("failing-worker", ["Fail"], dispatch_fn=fn)
    ticket = _make_ticket(tags=["Fail"])
    dispatched, worker = granny.route_ticket(ticket)
    assert dispatched is False
    assert worker == "failing-worker"


def test_route_ticket_tracks_status(granny):
    ticket = _make_ticket(id="T-track-test", tags=["Cognition"])
    granny.route_ticket(ticket)
    assert granny.get_status("T-track-test") == "routed"


# ── Hebbian strengthening ──────────────────────────────────────────────────────


def test_strengthen_edge_increases_weight(granny):
    granny.register_worker("learner", ["Learn"])
    initial = granny.get_edge_weights("Learn")[0][1]
    granny.strengthen_edge("Learn", "learner")
    after = granny.get_edge_weights("Learn")[0][1]
    assert after > initial


def test_weaken_edge_decreases_weight(granny):
    granny.register_worker("weak-worker", ["Weak"])
    granny.weaken_edge("Weak", "weak-worker")
    after = granny.get_edge_weights("Weak")[0][1]
    assert after < 1.0


def test_strengthen_edge_capped_at_ten(granny):
    granny.register_worker("max-worker", ["MaxTag"])
    for _ in range(200):
        granny.strengthen_edge("MaxTag", "max-worker", delta=1.0)
    weight = granny.get_edge_weights("MaxTag")[0][1]
    assert weight == 10.0


def test_weaken_edge_floored_at_point_one(granny):
    granny.register_worker("floor-worker", ["FloorTag"])
    for _ in range(200):
        granny.weaken_edge("FloorTag", "floor-worker", delta=1.0)
    weight = granny.get_edge_weights("FloorTag")[0][1]
    assert weight == 0.1


def test_routing_prefers_higher_weight_edge(granny):
    fn_a = MagicMock(return_value=True)
    fn_b = MagicMock(return_value=True)
    granny.register_worker("worker-a", ["Shared"], dispatch_fn=fn_a)
    granny.register_worker("worker-b", ["Shared"], dispatch_fn=fn_b)
    # Strengthen worker-b to make it preferred
    granny.strengthen_edge("Shared", "worker-b", delta=5.0)

    ticket = _make_ticket(tags=["Shared"])
    dispatched, worker = granny.route_ticket(ticket)
    assert worker == "worker-b"
    fn_b.assert_called_once()
    fn_a.assert_not_called()


# ── Status tracking ────────────────────────────────────────────────────────────


def test_track_and_get_status(granny):
    granny.track_status("T-123", "pending")
    assert granny.get_status("T-123") == "pending"


def test_get_status_unknown_ticket(granny):
    assert granny.get_status("T-nonexistent") is None


def test_list_statuses(granny):
    granny.track_status("T-a", "pending")
    granny.track_status("T-b", "routed")
    statuses = granny.list_statuses()
    assert statuses["T-a"] == "pending"
    assert statuses["T-b"] == "routed"


# ── health ────────────────────────────────────────────────────────────────────


def test_health_healthy_by_default(granny):
    assert granny.health()["status"] == "healthy"


def test_health_degraded_on_error(granny):
    granny._errors.append("something broke")
    assert granny.health()["status"] == "degraded"
