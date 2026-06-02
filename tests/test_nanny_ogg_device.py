"""Tests for devices/nanny/device.py — NannyOgg scheduler + dispatcher."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from devices.nanny.device import (
    AgentRegistration,
    NannyOggDevice,
    ScheduleEntry,
)

# ── ScheduleEntry dataclass ────────────────────────────────────────────────────


def test_schedule_entry_defaults():
    entry = ScheduleEntry(
        entry_id="test",
        condition_type="cron",
        condition_params={},
        action_type="post_channel",
        action_params={},
    )
    assert entry.enabled is True
    assert entry.last_fired is None
    assert entry.fire_count == 0


# ── Default schedule loads ─────────────────────────────────────────────────────


def test_default_schedule_loaded():
    nanny = NannyOggDevice()
    entries = nanny.list_entries()
    ids = [e.entry_id for e in entries]
    assert "weekly_audit_friday" in ids
    assert "alignment_review_5_cycles" in ids
    assert "consequence_gate_monitor" in ids
    assert "dreaming_daily" in ids


def test_default_schedule_has_five_entries():
    nanny = NannyOggDevice()
    assert len(nanny.list_entries()) == 5


# ── who_am_i / capabilities ────────────────────────────────────────────────────


def test_who_am_i_returns_device_id():
    nanny = NannyOggDevice()
    info = nanny.who_am_i()
    assert info["device_id"] == "nanny-ogg"


def test_capabilities_includes_emitted_keywords():
    nanny = NannyOggDevice()
    caps = nanny.capabilities()
    assert "NANNY_TRIGGER" in caps["emitted_keywords"]
    assert "NANNY_DISPATCH" in caps["emitted_keywords"]


# ── Schedule entry management ──────────────────────────────────────────────────


def test_add_entry_appends():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="my_entry",
        condition_type="cron",
        condition_params={"interval_hours": 1},
        action_type="post_channel",
        action_params={"channel": "shared", "message": "ping"},
    )
    nanny.add_entry(entry)
    ids = [e.entry_id for e in nanny.list_entries()]
    assert "my_entry" in ids


def test_add_entry_replaces_existing():
    nanny = NannyOggDevice()
    e1 = ScheduleEntry(
        entry_id="weekly_audit_friday",
        condition_type="cron",
        condition_params={"interval_hours": 24},
        action_type="post_channel",
        action_params={"channel": "shared", "message": "overridden"},
    )
    nanny.add_entry(e1)
    entries = [e for e in nanny.list_entries() if e.entry_id == "weekly_audit_friday"]
    assert len(entries) == 1
    assert entries[0].condition_params == {"interval_hours": 24}


def test_remove_entry_returns_true_on_success():
    nanny = NannyOggDevice()
    assert nanny.remove_entry("dreaming_daily") is True
    ids = [e.entry_id for e in nanny.list_entries()]
    assert "dreaming_daily" not in ids


def test_remove_entry_returns_false_when_not_found():
    nanny = NannyOggDevice()
    assert nanny.remove_entry("nonexistent") is False


# ── _condition_met: cron interval_hours ───────────────────────────────────────


def test_cron_interval_fires_when_never_run():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="x",
        condition_type="cron",
        condition_params={"interval_hours": 6},
        action_type="post_channel",
        action_params={},
    )
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is True


def test_cron_interval_does_not_fire_too_early():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="x",
        condition_type="cron",
        condition_params={"interval_hours": 6},
        action_type="post_channel",
        action_params={},
        last_fired="2026-05-28T10:00:00+00:00",
    )
    # Only 2 hours later — should not fire
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


def test_cron_interval_fires_when_elapsed():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="x",
        condition_type="cron",
        condition_params={"interval_hours": 6},
        action_type="post_channel",
        action_params={},
        last_fired="2026-05-28T06:00:00+00:00",
    )
    # 6 hours later — should fire
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is True


# ── _condition_met: cron weekday ───────────────────────────────────────────────


def test_cron_weekday_fires_on_matching_day_and_hour():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="weekly",
        condition_type="cron",
        condition_params={"weekday": 4, "hour": 18, "minute": 0},  # Friday 18:00
        action_type="post_channel",
        action_params={},
    )
    # 2026-05-29 is a Friday
    now = datetime(2026, 5, 29, 18, 0, 0, tzinfo=timezone.utc)
    assert now.weekday() == 4
    assert nanny._condition_met(entry, now) is True


def test_cron_weekday_does_not_fire_on_wrong_day():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="weekly",
        condition_type="cron",
        condition_params={"weekday": 4, "hour": 18, "minute": 0},
        action_type="post_channel",
        action_params={},
    )
    # 2026-05-28 is a Thursday (weekday=3)
    now = datetime(2026, 5, 28, 18, 0, 0, tzinfo=timezone.utc)
    assert now.weekday() == 3
    assert nanny._condition_met(entry, now) is False


def test_cron_weekday_does_not_fire_twice_same_day():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="weekly",
        condition_type="cron",
        condition_params={"weekday": 4, "hour": 18, "minute": 0},
        action_type="post_channel",
        action_params={},
        last_fired="2026-05-29T18:00:00+00:00",  # fired this Friday already
    )
    # Same Friday, 30 minutes later
    now = datetime(2026, 5, 29, 18, 30, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


# ── _condition_met: cron daily hour+minute ────────────────────────────────────


def test_cron_daily_fires_at_matching_hour():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="dreaming",
        condition_type="cron",
        condition_params={"hour": 3, "minute": 0},
        action_type="post_channel",
        action_params={},
    )
    now = datetime(2026, 5, 28, 3, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is True


def test_cron_daily_does_not_fire_at_wrong_hour():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="dreaming",
        condition_type="cron",
        condition_params={"hour": 3, "minute": 0},
        action_type="post_channel",
        action_params={},
    )
    now = datetime(2026, 5, 28, 4, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


# ── _condition_met: gate_date ──────────────────────────────────────────────────


def test_gate_date_fires_when_past():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="gate",
        condition_type="gate_date",
        condition_params={"gate_date": "2026-05-01T00:00:00"},
        action_type="fire_consequence",
        action_params={},
    )
    now = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is True


def test_gate_date_does_not_fire_before_gate():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="gate",
        condition_type="gate_date",
        condition_params={"gate_date": "2026-06-01T00:00:00"},
        action_type="fire_consequence",
        action_params={},
    )
    now = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


def test_gate_date_invalid_returns_false():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="gate",
        condition_type="gate_date",
        condition_params={"gate_date": "not-a-date"},
        action_type="fire_consequence",
        action_params={},
    )
    now = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


# ── _condition_met: threshold ──────────────────────────────────────────────────


def test_threshold_always_returns_false():
    """Threshold conditions are evaluated externally."""
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="alignment",
        condition_type="threshold",
        condition_params={"metric": "cycles_without_human_contact", "threshold": 5},
        action_type="post_channel",
        action_params={},
    )
    now = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)
    assert nanny._condition_met(entry, now) is False


# ── check_entries ──────────────────────────────────────────────────────────────


def test_check_entries_returns_triggered_entries():
    nanny = NannyOggDevice()
    # Remove existing entries, add one that should always fire (interval 0.0001h elapsed)
    for e in list(nanny.list_entries()):
        nanny.remove_entry(e.entry_id)

    entry = ScheduleEntry(
        entry_id="always_fire",
        condition_type="cron",
        condition_params={"interval_hours": 0.0001},
        action_type="post_channel",
        action_params={"channel": "shared", "message": "ping"},
    )
    nanny.add_entry(entry)

    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    triggered = nanny.check_entries(now=now)
    assert len(triggered) == 1
    assert triggered[0].entry_id == "always_fire"


def test_check_entries_skips_disabled_entries():
    nanny = NannyOggDevice()
    for e in list(nanny.list_entries()):
        nanny.remove_entry(e.entry_id)

    entry = ScheduleEntry(
        entry_id="disabled",
        condition_type="cron",
        condition_params={"interval_hours": 0.0001},
        action_type="post_channel",
        action_params={},
        enabled=False,
    )
    nanny.add_entry(entry)

    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    triggered = nanny.check_entries(now=now)
    assert triggered == []


# ── fire_entry ─────────────────────────────────────────────────────────────────


def test_fire_entry_post_channel_updates_last_fired():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="test_fire",
        condition_type="cron",
        condition_params={"interval_hours": 1},
        action_type="post_channel",
        action_params={"channel": "shared", "message": "NANNY_TRIGGER:test"},
    )

    with patch.object(nanny, "_post_to_channel") as mock_post:
        result = nanny.fire_entry(entry)

    assert result is True
    assert entry.last_fired is not None
    assert entry.fire_count == 1
    mock_post.assert_called_once_with("shared", "NANNY_TRIGGER:test")


def test_fire_entry_fire_consequence_action():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="consequence",
        condition_type="cron",
        condition_params={"interval_hours": 6},
        action_type="fire_consequence",
        action_params={},
    )

    with patch.object(nanny, "_check_consequence_gates") as mock_gates:
        result = nanny.fire_entry(entry)

    assert result is True
    mock_gates.assert_called_once()


def test_fire_entry_returns_false_on_exception():
    nanny = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="bad",
        condition_type="cron",
        condition_params={},
        action_type="post_channel",
        action_params={"channel": "shared", "message": "msg"},
    )

    with patch.object(nanny, "_post_to_channel", side_effect=RuntimeError("boom")):
        result = nanny.fire_entry(entry)

    assert result is False
    assert nanny._errors  # error was recorded


# ── Agent registry + route_world_ticket ───────────────────────────────────────


def test_register_agent_stores_registration():
    nanny = NannyOggDevice()
    nanny.register_agent("calendar-agent", ["Calendar", "WorldInteraction"])
    assert "calendar-agent" in nanny._agents
    assert "Calendar" in nanny._agents["calendar-agent"].handled_tags


def test_route_world_ticket_calls_dispatch_fn():
    nanny = NannyOggDevice()
    dispatch_fn = MagicMock(return_value=True)
    nanny.register_agent("calendar-agent", ["Calendar"], dispatch_fn=dispatch_fn)

    ticket = {"id": "T-add-event", "tags": ["Calendar"], "title": "Add meeting"}
    ok, agent_id = nanny.route_world_ticket(ticket)

    assert ok is True
    assert agent_id == "calendar-agent"
    dispatch_fn.assert_called_once_with(ticket)


def test_route_world_ticket_posts_channel_when_no_dispatch_fn():
    nanny = NannyOggDevice()
    nanny.register_agent("calendar-agent", ["Calendar"], dispatch_fn=None)

    ticket = {"id": "T-ev-99", "tags": ["Calendar"]}
    with patch.object(nanny, "_post_to_channel") as mock_post:
        ok, agent_id = nanny.route_world_ticket(ticket)

    assert ok is True
    assert agent_id == "calendar-agent"
    mock_post.assert_called_once()
    assert "T-ev-99" in mock_post.call_args[0][1]


def test_route_world_ticket_returns_no_agent_when_unregistered():
    nanny = NannyOggDevice()
    ticket = {"id": "T-unknown", "tags": ["Calendar"]}
    ok, agent_id = nanny.route_world_ticket(ticket)
    assert ok is False
    assert agent_id == "no_agent"


def test_route_world_ticket_handles_dispatch_exception():
    nanny = NannyOggDevice()

    def bad_dispatch(t):
        raise RuntimeError("network down")

    nanny.register_agent("calendar-agent", ["Calendar"], dispatch_fn=bad_dispatch)
    ticket = {"id": "T-fail", "tags": ["Calendar"]}
    ok, agent_id = nanny.route_world_ticket(ticket)
    assert ok is False
    assert agent_id == "calendar-agent"
    assert nanny._errors


# ── health / self_test ─────────────────────────────────────────────────────────


def test_health_degraded_when_errors():
    nanny = NannyOggDevice()
    nanny._errors.append("something went wrong")
    h = nanny.health()
    assert h["status"] == "degraded"
    assert "something went wrong" in h["detail"]


def test_health_degraded_when_poll_thread_not_running():
    nanny = NannyOggDevice()
    h = nanny.health()
    assert h["status"] == "degraded"  # thread never started


def test_self_test_reports_entry_count():
    nanny = NannyOggDevice()
    result = nanny.self_test()
    assert result["passed"] is True
    assert "5" in result["details"]


# ── start / stop ───────────────────────────────────────────────────────────────


def test_start_launches_poll_thread():
    nanny = NannyOggDevice()
    with patch.object(nanny, "_start_poll_thread") as mock_start:
        result = nanny.start()
    assert result is True
    mock_start.assert_called_once()


def test_stop_sets_stop_event():
    nanny = NannyOggDevice()
    result = nanny.stop()
    assert result is True
    assert nanny._stop_event.is_set()


def test_recovery_clears_errors_and_restarts():
    nanny = NannyOggDevice()
    nanny._errors.append("prior error")
    with patch.object(nanny, "restart") as mock_restart:
        nanny.recovery()
    assert nanny._errors == []
    mock_restart.assert_called_once()
