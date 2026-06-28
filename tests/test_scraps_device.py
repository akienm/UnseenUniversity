"""Unit tests for ScrapsDevice and validation_rules."""

from __future__ import annotations

import json
import pytest

from unseen_university.devices.scraps.scraps_device import ScrapsDevice
from unseen_university.devices.scraps import validation_rules

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
        result = d.validate_ticket(
            {"title": "Add thing", "description": ""}, silent=True
        )
        assert result["valid"] is False
        assert any("description" in i for i in result["issues"])
        assert result["validated_at"] is None

    def test_generic_title_invalid(self):
        d = ScrapsDevice()
        result = d.validate_ticket(
            {
                "title": "TODO",
                "description": "**Test plan:** whatever. " + "x" * 40,
            },
            silent=True,
        )
        assert result["valid"] is False
        assert any("title" in i for i in result["issues"])

    def test_no_structured_section_invalid(self):
        d = ScrapsDevice()
        result = d.validate_ticket(
            {
                "title": "A meaningful title here",
                "description": "just a plain sentence with enough chars to pass length check.",
            },
            silent=True,
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


# ── ScrapsDevice channel posts ────────────────────────────────────────────────


class TestScrapsChannelPosts:
    """Scraps posts to shared channel on validation failure and fuzzy escalation."""

    def _scraps_with_captured_posts(self):
        d = ScrapsDevice()
        posts = []

        def fake_post(channel, message):
            posts.append((channel, message))

        d._post = fake_post
        return d, posts

    def test_validation_failure_posts_to_channel(self):
        d, posts = self._scraps_with_captured_posts()
        d.validate_ticket({"title": "fix", "description": ""})
        assert posts, "expected channel post on validation failure"
        _, msg = posts[-1]
        assert "failed" in msg.lower() or "validation" in msg.lower()
        assert "Scraps" in msg

    def test_fuzzy_escalation_posts_to_channel(self):
        d, posts = self._scraps_with_captured_posts()
        # Short description triggers fuzzy check — patch _fuzzy_check to avoid inference
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "unseen_university.devices.scraps.scraps_device._fuzzy_check",
                lambda t: (False, "INVALID: not enough detail"),
            )
            d.validate_ticket(
                {
                    "title": "Add meaningful feature",
                    "description": "**Test plan:** short",
                }
            )
        fuzzy_posts = [msg for _, msg in posts if "fuzzy" in msg.lower()]
        assert fuzzy_posts, "expected fuzzy check channel post"

    def test_valid_ticket_no_failure_post(self):
        d, posts = self._scraps_with_captured_posts()
        d.validate_ticket(_GOOD_TICKET)
        failure_posts = [msg for _, msg in posts if "failed" in msg.lower()]
        assert not failure_posts, "should not post failure for valid ticket"

    def test_silent_produces_no_channel_posts(self):
        d, posts = self._scraps_with_captured_posts()
        d.validate_ticket({"title": "bad"}, silent=True)
        assert not posts, "silent=True must suppress all channel posts"

    def test_capabilities_can_send_true(self):
        d = ScrapsDevice()
        assert d.capabilities()["can_send"] is True


# ── embed_text ────────────────────────────────────────────────────────────────


class TestEmbedText:
    def test_returns_vector_model_dimension(self):
        d = ScrapsDevice()
        result = d.embed_text("hello world")
        assert "vector" in result
        assert "model" in result
        assert "dimension" in result

    def test_vector_length_matches_dimension(self):
        d = ScrapsDevice()
        result = d.embed_text("test string")
        assert len(result["vector"]) == result["dimension"]

    def test_fallback_fires_without_openai_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_KEY", raising=False)
        d = ScrapsDevice()
        result = d.embed_text("fallback test")
        assert result["model"] == "hash-sha256-384"
        assert result["dimension"] == 384

    def test_model_param_accepted(self):
        d = ScrapsDevice()
        result = d.embed_text("hello", model="auto")
        assert "vector" in result

    def test_capabilities_lists_embed_endpoint(self):
        d = ScrapsDevice()
        caps = d.capabilities()
        assert "scraps_embed_text" in caps["mcp_endpoints"]
        assert "scraps_validate_ticket" in caps["mcp_endpoints"]
