"""Tests for DickSimnel tier cascade — builder → creator → CC.

Covers T-dicksimnel-tier-routing completion criteria:
  1. rules_engine.py has a named creator-tier rule
  2. Mock escalation chain produces expected summary structure
  3. Cumulative context: master tier receives both builder + creator summaries
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from unseen_university.devices.inference.rules_engine import _DEFAULT_RULES, RoutingRule


# ── 1. rules_engine has creator-tier rules ────────────────────────────────────


class TestCreatorTierRulesExist:
    def test_creator_rules_present(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        assert len(creator_rules) >= 1, "No creator-tier rules found in _DEFAULT_RULES"

    def test_creator_primary_rule_label(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        labels = [r.label for r in creator_rules]
        assert any("creator" in lbl for lbl in labels)

    def test_creator_rules_have_openrouter_source(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        sources = {r.source_name for r in creator_rules}
        assert "openrouter" in sources

    def test_creator_rule_priorities_are_ordered(self):
        creator_rules = sorted(
            [r for r in _DEFAULT_RULES if r.task_class == "creator"],
            key=lambda r: r.priority,
        )
        # Primary rule must have lower priority number than fallback
        assert creator_rules[0].priority < creator_rules[-1].priority

    def test_creator_tier_distinct_from_worker(self):
        creator_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "creator"}
        worker_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "worker"}
        # At least one creator model should differ from worker models
        assert creator_model_ids, "No creator rules found"
        # They should not be identical sets (creator exists as its own tier)
        assert creator_model_ids != worker_model_ids


# ── 2. builder escalates to creator with context ──────────────────────────────


def _make_device_with_cascade(cascade):
    """Return a DickSimnelDevice with a patched _TIER_CASCADE."""
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice
    dev = DickSimnelDevice.__new__(DickSimnelDevice)
    dev._TIER_CASCADE = cascade
    dev._active_ticket = None
    return dev


class TestBuilderEscalatesToCreator:
    def test_escalate_advances_to_creator_tier(self):
        """When builder returns ESCALATE:, the next tier (creator) is attempted."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("builder-model", "builder"),
            ("creator-model", "creator"),
        ]
        ticket = {"id": "T-test", "description": "initial desc", "tags": []}

        call_log = []

        def fake_toolloop_run(t, system_prompt, model_override=None):
            call_log.append(model_override)
            if model_override == "builder-model":
                return "ESCALATE: too complex for builder"
            return "DONE: creator handled it"

        loop_mock = MagicMock()
        loop_mock.run.side_effect = lambda t, sp, model_override=None: fake_toolloop_run(t, sp, model_override)
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                result = dev._run_inference(ticket)

        assert call_log == ["builder-model", "creator-model"], (
            f"Expected builder then creator calls, got {call_log}"
        )
        assert result == "DONE: creator handled it"

    def test_escalation_context_appended_to_ticket(self):
        """Builder escalation reason is appended to ticket description for creator."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("builder-model", "builder"),
            ("creator-model", "creator"),
        ]
        ticket = {"id": "T-ctx", "description": "original desc", "tags": []}

        received_tickets = []

        def capture_run(t, system_prompt, model_override=None):
            received_tickets.append(dict(t))
            if model_override == "builder-model":
                return "ESCALATE: needs bigger model"
            return "DONE: ok"

        loop_mock = MagicMock()
        loop_mock.run.side_effect = lambda t, sp, model_override=None: capture_run(t, sp, model_override)
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                dev._run_inference(ticket)

        assert len(received_tickets) == 2
        builder_ticket = received_tickets[0]
        creator_ticket = received_tickets[1]

        assert builder_ticket["description"] == "original desc"
        assert "builder tier escalation" in creator_ticket["description"]
        assert "needs bigger model" in creator_ticket["description"]

    def test_creator_done_does_not_reach_master(self):
        """When creator returns DONE:, the cascade stops — no third tier attempted."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("builder-model", "builder"),
            ("creator-model", "creator"),
            ("master-model", "master"),
        ]
        ticket = {"id": "T-stop", "description": "desc", "tags": []}

        call_log = []

        def fake_run(t, system_prompt, model_override=None):
            call_log.append(model_override)
            if model_override == "builder-model":
                return "ESCALATE: too hard"
            if model_override == "creator-model":
                return "DONE: creator solved it"
            return "DONE: master would have done this too"

        loop_mock = MagicMock()
        loop_mock.run.side_effect = lambda t, sp, model_override=None: fake_run(t, sp, model_override)
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                result = dev._run_inference(ticket)

        assert "master-model" not in call_log, "Master was called but creator already returned DONE:"
        assert result == "DONE: creator solved it"


# ── 3. Cumulative context: master sees both builder + creator attempts ─────────


class TestCumulativeContext:
    def test_master_receives_cumulative_escalation_notes(self):
        """When builder and creator both escalate, master gets both escalation notes."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("builder-model", "builder"),
            ("creator-model", "creator"),
            ("master-model", "master"),
        ]
        ticket = {"id": "T-cumul", "description": "original", "tags": []}

        received_tickets = []

        def capture_run(t, system_prompt, model_override=None):
            received_tickets.append(dict(t))
            if model_override == "builder-model":
                return "ESCALATE: builder gave up"
            if model_override == "creator-model":
                return "ESCALATE: creator gave up too"
            return "DONE: master handled it"

        loop_mock = MagicMock()
        loop_mock.run.side_effect = lambda t, sp, model_override=None: capture_run(t, sp, model_override)
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                result = dev._run_inference(ticket)

        assert len(received_tickets) == 3
        master_ticket = received_tickets[2]

        assert "builder tier escalation" in master_ticket["description"]
        assert "builder gave up" in master_ticket["description"]
        assert "creator tier escalation" in master_ticket["description"]
        assert "creator gave up too" in master_ticket["description"]
        assert result == "DONE: master handled it"

    def test_last_tier_escalate_returns_immediately(self):
        """When the last tier returns ESCALATE:, it returns without advancing."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("only-model", "builder"),
        ]
        ticket = {"id": "T-last", "description": "desc", "tags": []}

        loop_mock = MagicMock()
        loop_mock.run.return_value = "ESCALATE: cannot do this"
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                result = dev._run_inference(ticket)

        assert result == "ESCALATE: cannot do this"

    def test_done_at_first_tier_skips_rest(self):
        """DONE: at builder tier should not call any further tiers."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice

        cascade = [
            ("builder-model", "builder"),
            ("creator-model", "creator"),
        ]
        ticket = {"id": "T-fast", "description": "desc", "tags": []}

        call_log = []

        def fake_run(t, system_prompt, model_override=None):
            call_log.append(model_override)
            return "DONE: builder nailed it"

        loop_mock = MagicMock()
        loop_mock.run.side_effect = lambda t, sp, model_override=None: fake_run(t, sp, model_override)
        loop_mock._turn_log = []

        with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"):
            with patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop:
                MockLoop.return_value = loop_mock

                dev = _make_device_with_cascade(cascade)
                result = dev._run_inference(ticket)

        assert call_log == ["builder-model"]
        assert result == "DONE: builder nailed it"
