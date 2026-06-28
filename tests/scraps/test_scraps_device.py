"""
Unit tests for ScrapsDevice and validation_rules.

ScrapsDevice is fully in-process (no DB, no subprocess). Tests exercise
validate_ticket(), the BaseDevice contract, and all validation rule checks.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.scraps.scraps_device import ScrapsDevice
from unseen_university.devices.scraps import validation_rules
from unseen_university.device import INTERFACE_VERSION

_VALID_TICKET = {
    "id": "T-test-001",
    "title": "Add retry logic to broker.py",
    "description": "**Affected files:** broker.py\n\nAdd exponential backoff retry when the IMAP connection drops. Limit to 3 retries.",
}

_MISSING_DESC_TICKET = {
    "id": "T-test-002",
    "title": "Fix the thing",
    "description": "",
}

_GENERIC_TITLE_TICKET = {
    "id": "T-test-003",
    "title": "fix",
    "description": "**Affected files:** foo.py\n\nSomething needs to be done about the connection pool.",
}

_NO_SECTION_TICKET = {
    "id": "T-test-004",
    "title": "Add feature X",
    "description": "Some loose description with no structured sections at all here.",
}


@pytest.fixture
def device():
    return ScrapsDevice()


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_required_keys(device):
    info = device.who_am_i()
    assert info["device_id"] == "scraps"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(device):
    reqs = device.requirements()
    assert "deps" in reqs


def test_capabilities_has_required_keys(device):
    caps = device.capabilities()
    for key in ("can_send", "can_receive", "emitted_keywords"):
        assert key in caps


def test_comms_has_required_keys(device):
    c = device.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_health_returns_healthy(device):
    h = device.health()
    assert h["status"] == "healthy"
    assert "detail" in h
    assert "checked_at" in h


def test_uptime_positive(device):
    import time

    time.sleep(0.01)
    assert device.uptime() > 0


def test_startup_errors_is_list(device):
    assert isinstance(device.startup_errors(), list)


def test_logs_has_paths_key(device):
    assert "paths" in device.logs()


def test_update_info_required_keys(device):
    info = device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_required_keys(device):
    w = device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_restart_does_not_raise(device):
    device.restart()


def test_block_changes_health_to_unhealthy(device):
    device.block("test")
    assert device.health()["status"] == "unhealthy"


def test_halt_does_not_raise(device):
    device.halt()  # ScrapsDevice halt is a no-op (in-process, nothing to halt)


def test_recovery_restores_health(device):
    device.block("test")
    device.recovery()
    assert device.health()["status"] == "healthy"


# ── validate_ticket() ─────────────────────────────────────────────────────────


def test_valid_ticket_passes(device):
    result = device.validate_ticket(_VALID_TICKET, silent=True)
    assert result["valid"] is True
    assert result["issues"] == []
    assert result["validated_at"] is not None


def test_empty_description_fails(device):
    result = device.validate_ticket(_MISSING_DESC_TICKET, silent=True)
    assert result["valid"] is False
    assert result["issues"]
    assert result["validated_at"] is None


def test_generic_title_fails(device):
    result = device.validate_ticket(_GENERIC_TITLE_TICKET, silent=True)
    assert result["valid"] is False


def test_no_structured_section_fails(device):
    result = device.validate_ticket(_NO_SECTION_TICKET, silent=True)
    assert result["valid"] is False
    assert any("structured section" in issue for issue in result["issues"])


def test_validated_at_is_iso_timestamp(device):
    result = device.validate_ticket(_VALID_TICKET, silent=True)
    import re

    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result["validated_at"])


# ── validation_rules unit tests ───────────────────────────────────────────────


class TestCheckNonEmptyDescription:
    def test_empty_returns_issue(self):
        issues = validation_rules.check_nonempty_description({"description": ""})
        assert issues

    def test_none_returns_issue(self):
        issues = validation_rules.check_nonempty_description({"description": None})
        assert issues

    def test_short_returns_issue(self):
        issues = validation_rules.check_nonempty_description({"description": "hi"})
        assert issues
        assert "too short" in issues[0]

    def test_adequate_description_passes(self):
        issues = validation_rules.check_nonempty_description(
            {"description": "This is a sufficiently long description for the ticket."}
        )
        assert issues == []


class TestCheckNonGenericTitle:
    def test_missing_title_fails(self):
        issues = validation_rules.check_nongeneric_title({})
        assert issues

    def test_generic_fix_fails(self):
        issues = validation_rules.check_nongeneric_title({"title": "fix"})
        assert issues

    def test_generic_todo_fails(self):
        issues = validation_rules.check_nongeneric_title({"title": "todo"})
        assert issues

    def test_generic_task_fails(self):
        issues = validation_rules.check_nongeneric_title({"title": "task"})
        assert issues

    def test_descriptive_title_passes(self):
        issues = validation_rules.check_nongeneric_title(
            {"title": "Add retry logic to IMAP connection handler"}
        )
        assert issues == []

    def test_very_short_title_fails(self):
        issues = validation_rules.check_nongeneric_title({"title": "hi"})
        assert issues


class TestCheckHasStructuredSection:
    def test_affected_files_passes(self):
        issues = validation_rules.check_has_structured_section(
            {"description": "**Affected files:** broker.py — adds retry logic"}
        )
        assert issues == []

    def test_test_plan_passes(self):
        issues = validation_rules.check_has_structured_section(
            {"description": "**Test plan:** run pytest tests/bus/"}
        )
        assert issues == []

    def test_markdown_header_passes(self):
        issues = validation_rules.check_has_structured_section(
            {"description": "## Goal\nDo the thing\n## Test plan\nRun tests"}
        )
        assert issues == []

    def test_bold_section_passes(self):
        issues = validation_rules.check_has_structured_section(
            {"description": "**Goal:** fix the bug in broker.py"}
        )
        assert issues == []

    def test_prose_only_fails(self):
        issues = validation_rules.check_has_structured_section(
            {"description": "just some loose prose here with no structure at all"}
        )
        assert issues
        assert "structured section" in issues[0]


class TestCheckHasIntention:
    def test_missing_field_returns_issue(self):
        issues = validation_rules.check_has_intention({})
        assert issues
        assert "intention" in issues[0]

    def test_none_value_returns_issue(self):
        issues = validation_rules.check_has_intention({"intention": None})
        assert issues

    def test_empty_string_returns_issue(self):
        issues = validation_rules.check_has_intention({"intention": ""})
        assert issues

    def test_whitespace_only_returns_issue(self):
        issues = validation_rules.check_has_intention({"intention": "   "})
        assert issues

    def test_valid_intention_passes(self):
        issues = validation_rules.check_has_intention(
            {"intention": "I intend that the ticket schema enforces IBD root artifacts."}
        )
        assert issues == []


class TestRunAllWithAdvisory:
    def test_valid_ticket_no_issues(self):
        blocking, advisory = validation_rules.run_all_with_advisory(
            {
                "intention": "I intend that retry logic is added.",
                "title": "Add retry logic to IMAP handler",
                "description": "**Affected files:** broker.py — adds exponential backoff retry.",
            }
        )
        assert blocking == []
        assert advisory == []

    def test_missing_intention_is_advisory_only(self):
        blocking, advisory = validation_rules.run_all_with_advisory(
            {
                "title": "Add retry logic to IMAP handler",
                "description": "**Affected files:** broker.py — adds retry logic.",
            }
        )
        assert blocking == []
        assert advisory  # missing intention is advisory, not blocking

    def test_run_all_unchanged_by_advisory(self):
        # run_all() must not include advisory checks — backward compat
        issues = validation_rules.run_all({"intention": "I intend this."})
        # run_all only checks description, title, structure — not intention
        assert all("intention" not in i for i in issues)


class TestRunAll:
    def test_fully_valid_ticket_passes(self):
        issues = validation_rules.run_all(_VALID_TICKET)
        assert issues == []

    def test_invalid_ticket_returns_all_issues(self):
        issues = validation_rules.run_all({"title": "", "description": ""})
        assert len(issues) >= 2  # missing title + missing description

    def test_only_description_issue_returned(self):
        t = dict(_VALID_TICKET, description="")
        issues = validation_rules.run_all(t)
        assert any("description" in i for i in issues)
