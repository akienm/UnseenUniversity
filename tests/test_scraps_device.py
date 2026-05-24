"""Unit tests for ScrapsDevice and validation_rules."""

from __future__ import annotations

import json
import pytest

from devices.scraps.scraps_device import ScrapsDevice
from devices.scraps import validation_rules

# ── validation_rules ─────────────────────────────────────────────────────────


class TestCheckNonEmptyDescription:
    def test_empty_fails(self):
        assert validation_rules.check_nonempty_description({"description": ""}) != []

    def test_missing_fails(self):
        assert validation_rules.check_nonempty_description({}) != []

    def test_short_fails(self):
        assert validation_rules.check_nonempty_description({"description": "hi"}) != []

    def test_adequate_passes(self):
        assert (
            validation_rules.check_nonempty_description({"description": "x" * 25}) == []
        )


class TestCheckNonGenericTitle:
    def test_missing_fails(self):
        assert validation_rules.check_nongeneric_title({}) != []

    def test_generic_fix_fails(self):
        assert validation_rules.check_nongeneric_title({"title": "fix"}) != []

    def test_generic_todo_fails(self):
        assert validation_rules.check_nongeneric_title({"title": "TODO"}) != []

    def test_generic_placeholder_fails(self):
        assert validation_rules.check_nongeneric_title({"title": "placeholder"}) != []

    def test_high_work_fails(self):
        assert validation_rules.check_nongeneric_title({"title": "HIGH work"}) != []

    def test_descriptive_passes(self):
        assert (
            validation_rules.check_nongeneric_title(
                {"title": "Add scraps device to rack"}
            )
            == []
        )


class TestCheckHasStructuredSection:
    def test_no_section_fails(self):
        result = validation_rules.check_has_structured_section(
            {"description": "just a plain sentence with no structure"}
        )
        assert result != []

    def test_test_plan_bold_passes(self):
        result = validation_rules.check_has_structured_section(
            {"description": "**Test plan:** run pytest"}
        )
        assert result == []

    def test_markdown_header_passes(self):
        result = validation_rules.check_has_structured_section(
            {"description": "## Approach\ndoes something"}
        )
        assert result == []

    def test_affected_files_passes(self):
        result = validation_rules.check_has_structured_section(
            {"description": "**Affected files:** scraps_device.py"}
        )
        assert result == []


# ── ScrapsDevice.validate_ticket ─────────────────────────────────────────────

_GOOD_TICKET = {
    "title": "Add Scraps rack device",
    "description": (
        "Build the Scraps gatekeeper device.\n\n"
        "**Test plan:** validate_ticket returns valid=True for well-formed tickets.\n"
        "**Affected files:** devices/scraps/scraps_device.py"
    ),
}


class TestValidateTicket:
    def test_good_ticket_is_valid(self):
        d = ScrapsDevice()
        result = d.validate_ticket(_GOOD_TICKET)
        assert result["valid"] is True
        assert result["issues"] == []
        assert result["validated_at"] is not None

    def test_empty_description_invalid(self):
        d = ScrapsDevice()
        result = d.validate_ticket({"title": "Add thing", "description": ""})
        assert result["valid"] is False
        assert any("description" in i for i in result["issues"])
        assert result["validated_at"] is None

    def test_generic_title_invalid(self):
        d = ScrapsDevice()
        result = d.validate_ticket(
            {
                "title": "TODO",
                "description": "**Test plan:** whatever. " + "x" * 40,
            }
        )
        assert result["valid"] is False
        assert any("title" in i for i in result["issues"])

    def test_no_structured_section_invalid(self):
        d = ScrapsDevice()
        result = d.validate_ticket(
            {
                "title": "A meaningful title here",
                "description": "just a plain sentence with enough chars to pass length check.",
            }
        )
        assert result["valid"] is False
        assert any("section" in i for i in result["issues"])

    def test_returns_expected_keys(self):
        d = ScrapsDevice()
        result = d.validate_ticket(_GOOD_TICKET)
        assert set(result.keys()) == {"valid", "issues", "validated_at"}


# ── scraps_tools MCP dispatch ─────────────────────────────────────────────────


class TestScrapsTools:
    def test_dispatch_returns_json(self):
        from unseen_university.devices.librarian.tools import scraps_tools

        raw = scraps_tools.dispatch("scraps_validate_ticket", {"ticket": _GOOD_TICKET})
        assert raw is not None
        parsed = json.loads(raw)
        assert "valid" in parsed

    def test_dispatch_unknown_returns_none(self):
        from unseen_university.devices.librarian.tools import scraps_tools

        assert scraps_tools.dispatch("unknown_tool", {}) is None

    def test_dispatch_bad_input(self):
        from unseen_university.devices.librarian.tools import scraps_tools

        raw = scraps_tools.dispatch("scraps_validate_ticket", {"ticket": "not a dict"})
        assert raw is not None
        parsed = json.loads(raw)
        assert "error" in parsed

    def test_schema_present_in_librarian(self):
        from unseen_university.devices.librarian import tools as _tools

        names = [s["name"] for s in _tools.SCHEMAS]
        assert "scraps_validate_ticket" in names
