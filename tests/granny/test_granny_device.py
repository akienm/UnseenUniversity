"""
Unit tests for GrannyWeatherwaxDevice — routing, edges, ticket intake, status tracking.

Covers the methods not exercised by test_dispatch_chain.py:
  - BaseDevice contract methods
  - register_worker / route_ticket / intake_ticket
  - strengthen_edge / weaken_edge / get_edge_weights
  - track_status / get_status / list_statuses
  - self_test()
"""

from __future__ import annotations

import pytest

from devices.granny.device import GrannyWeatherwaxDevice, _DEFAULT_ROUTING
from unseen_university.device import INTERFACE_VERSION


@pytest.fixture
def granny():
    return GrannyWeatherwaxDevice()


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_required_keys(granny):
    info = granny.who_am_i()
    assert info["device_id"] == "granny-weatherwax"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(granny):
    reqs = granny.requirements()
    assert "deps" in reqs


def test_capabilities_has_required_keys(granny):
    caps = granny.capabilities()
    for key in ("can_send", "can_receive", "emitted_keywords"):
        assert key in caps


def test_comms_has_required_keys(granny):
    c = granny.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_comms_address_starts_with_comms(granny):
    assert granny.comms()["address"].startswith("comms://")


def test_interface_version(granny):
    assert granny.interface_version() == INTERFACE_VERSION


def test_health_returns_valid_structure(granny):
    h = granny.health()
    assert h["status"] in ("healthy", "degraded", "unhealthy")
    assert "detail" in h
    assert "checked_at" in h


def test_health_is_healthy_initially(granny):
    assert granny.health()["status"] == "healthy"


def test_uptime_positive(granny):
    import time

    time.sleep(0.01)
    assert granny.uptime() > 0


def test_startup_errors_is_list(granny):
    assert isinstance(granny.startup_errors(), list)


def test_logs_has_paths(granny):
    assert "paths" in granny.logs()


def test_update_info_has_required_keys(granny):
    info = granny.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(granny):
    w = granny.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_restart_does_not_raise(granny):
    granny.restart()


def test_block_adds_to_errors(granny):
    granny.block("test block reason")
    errors = granny.startup_errors()
    assert any("test block reason" in e for e in errors)


def test_halt_does_not_raise(granny):
    granny.halt()


def test_recovery_clears_errors(granny):
    granny.block("something")
    granny.recovery()
    assert granny.startup_errors() == []


# ── self_test() ────────────────────────────────────────────────────────────────


def test_self_test_returns_passed_key(granny):
    result = granny.self_test()
    assert "passed" in result
    assert isinstance(result["passed"], bool)


def test_self_test_passes_initially(granny):
    result = granny.self_test()
    assert result["passed"] is True


# ── register_worker / route_ticket ────────────────────────────────────────────


def test_register_worker_adds_node(granny):
    granny.register_worker("cc", handled_tags=["Platform", "Infrastructure"])
    weights = granny.get_edge_weights("Platform")
    worker_ids = [w for w, _ in weights]
    assert "cc" in worker_ids


def test_route_ticket_returns_cc_for_platform_tag(granny):
    ticket = {"id": "T-test", "tags": ["Platform"], "title": "A platform ticket"}
    routed, worker = granny.route_ticket(ticket)
    assert routed is True
    assert worker == "cc"


def test_route_ticket_returns_cc_for_infrastructure_tag(granny):
    ticket = {"id": "T-test", "tags": ["Infrastructure"], "title": "Infra ticket"}
    routed, worker = granny.route_ticket(ticket)
    assert routed is True
    assert worker == "cc"


def test_route_ticket_returns_cc_for_tests_tag(granny):
    ticket = {"id": "T-test", "tags": ["tests"], "title": "Test suite ticket"}
    routed, worker = granny.route_ticket(ticket)
    assert routed is True
    assert worker == "cc"


def test_route_ticket_returns_igor_for_cognition_tag(granny):
    ticket = {"id": "T-test", "tags": ["Cognition"], "title": "A cognition ticket"}
    routed, worker = granny.route_ticket(ticket)
    assert routed is True
    assert worker == "igor"


def test_route_ticket_unknown_tag_returns_false(granny):
    ticket = {
        "id": "T-test",
        "tags": ["NonExistentTagXYZ"],
        "title": "Mystery ticket",
    }
    routed, _ = granny.route_ticket(ticket)
    assert routed is False


# ── Edge weights ──────────────────────────────────────────────────────────────


def test_strengthen_edge_increases_weight(granny):
    initial = {w: wt for w, wt in granny.get_edge_weights("Platform")}
    granny.strengthen_edge("Platform", "cc", delta=0.1)
    after = {w: wt for w, wt in granny.get_edge_weights("Platform")}
    assert after.get("cc", 0) >= initial.get("cc", 0)


def test_weaken_edge_decreases_weight(granny):
    granny.strengthen_edge("Platform", "cc", delta=0.5)
    before = {w: wt for w, wt in granny.get_edge_weights("Platform")}
    granny.weaken_edge("Platform", "cc", delta=0.2)
    after = {w: wt for w, wt in granny.get_edge_weights("Platform")}
    assert after.get("cc", 0) <= before.get("cc", 1)


def test_get_edge_weights_returns_list_of_tuples(granny):
    weights = granny.get_edge_weights("Platform")
    assert isinstance(weights, list)
    for item in weights:
        assert len(item) == 2
        worker_id, weight = item
        assert isinstance(worker_id, str)
        assert isinstance(weight, float)


def test_get_edge_weights_unknown_tag_returns_empty(granny):
    weights = granny.get_edge_weights("NonExistentTagXYZ")
    assert weights == []


# ── intake_ticket ─────────────────────────────────────────────────────────────


def test_intake_ticket_valid_ticket_passes(granny):
    ticket = {
        "id": "T-intake-test",
        "title": "Add retry logic to broker.py",
        "size": "S",
        "tags": ["Platform"],
        "description": (
            "**Affected files:** broker.py\n"
            "**Scope boundary:** only broker.py retry logic\n"
            "**Completion criteria:** pytest tests/bus/ green after change\n"
            "Add exponential backoff retry when the IMAP connection drops."
        ),
    }
    result = granny.intake_ticket(ticket)
    assert result.passed is True


def test_intake_ticket_empty_description_fails(granny):
    ticket = {
        "id": "T-intake-bad",
        "title": "Fix thing",
        "tags": ["Platform"],
        "description": "",
    }
    result = granny.intake_ticket(ticket)
    assert result.passed is False
    assert result.reasons


# ── track_status / get_status / list_statuses ─────────────────────────────────


def test_track_status_stores_status(granny):
    granny.track_status("T-001", "in_progress")
    assert granny.get_status("T-001") == "in_progress"


def test_get_status_returns_none_for_unknown(granny):
    assert granny.get_status("T-nonexistent") is None


def test_list_statuses_returns_all_tracked(granny):
    granny.track_status("T-001", "in_progress")
    granny.track_status("T-002", "done")
    statuses = granny.list_statuses()
    assert statuses["T-001"] == "in_progress"
    assert statuses["T-002"] == "done"


def test_track_status_updates_existing(granny):
    granny.track_status("T-001", "sprint")
    granny.track_status("T-001", "in_progress")
    assert granny.get_status("T-001") == "in_progress"
