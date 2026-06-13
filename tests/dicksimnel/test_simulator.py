"""
Tests for devices/dicksimnel/simulator.py (TicketSimulator).

Tests:
- replay_all: iterates all turns in order for a closed ticket
- replay_all: returns empty for ticket with no log dir
- answer_tool_call: returns cached result on cache hit
- answer_tool_call: returns CC shim placeholder on cache miss
- decision_points: extracts only events with non-empty decision_point
- success_rate: computes correct fraction
- record_outcome: updates event in-place
- replay on 3 test datasets (T-test-closed-ticket, T-realistic-test, T-error-pattern-test)
- device replay_and_analyze: returns structured result dict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from devices.dicksimnel.simulator import Event, TicketSimulator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_turns(log_dir: Path, events: list[dict]) -> None:
    """Write a list of event dicts to turn_001.jsonl in log_dir."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl = log_dir / "turn_001.jsonl"
    jsonl.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _make_sim(tmp_path: Path, ticket_id: str, events: list[dict]) -> TicketSimulator:
    """Create a TicketSimulator whose _LOGS_ROOT is redirected to tmp_path."""
    _write_turns(tmp_path / ticket_id, events)
    with patch("devices.dicksimnel.simulator._LOGS_ROOT", tmp_path):
        return TicketSimulator(ticket_id)


_SAMPLE_EVENTS = [
    {
        "timestamp": "2026-06-12T10:00:00Z",
        "turn": 1,
        "decision_point": "tool_selection",
        "tool_name": "read",
        "tool_args": {"path": "/etc/config.json"},
        "tool_result": "ERROR: not found",
        "outcome": "failure",
    },
    {
        "timestamp": "2026-06-12T10:00:05Z",
        "turn": 2,
        "decision_point": "tool_selection",
        "tool_name": "write",
        "tool_args": {"path": "/etc/config.json", "content": "{}"},
        "tool_result": "wrote 2 bytes",
        "outcome": "success",
    },
    {
        "timestamp": "2026-06-12T10:00:10Z",
        "turn": 3,
        "decision_point": "tool_selection",
        "tool_name": "read",
        "tool_args": {"path": "/etc/config.json"},
        "tool_result": "{}",
        "outcome": "success",
    },
]


# ── replay_all ────────────────────────────────────────────────────────────────

def test_replay_all_returns_events_in_order(tmp_path):
    sim = _make_sim(tmp_path, "T-replay-order", _SAMPLE_EVENTS)
    events = list(sim.replay_all())
    assert len(events) == 3
    assert [e.turn_num for e in events] == [1, 2, 3]


def test_replay_all_empty_when_no_log_dir(tmp_path):
    with patch("devices.dicksimnel.simulator._LOGS_ROOT", tmp_path):
        sim = TicketSimulator("T-no-logs")
    events = list(sim.replay_all())
    assert events == []


def test_replay_all_event_fields(tmp_path):
    sim = _make_sim(tmp_path, "T-fields", _SAMPLE_EVENTS)
    first = list(sim.replay_all())[0]
    assert first.turn_num == 1
    assert first.tool_name == "read"
    assert first.decision_point == "tool_selection"
    assert first.outcome == "failure"


# ── answer_tool_call ──────────────────────────────────────────────────────────

def test_answer_tool_call_cache_hit(tmp_path):
    sim = _make_sim(tmp_path, "T-cache-hit", _SAMPLE_EVENTS)
    result = sim.answer_tool_call("write", {"path": "/etc/config.json", "content": "{}"})
    assert result == "wrote 2 bytes"


def test_answer_tool_call_cache_miss_returns_shim_placeholder(tmp_path):
    sim = _make_sim(tmp_path, "T-cache-miss", _SAMPLE_EVENTS)
    result = sim.answer_tool_call("nonexistent_tool", {"x": 1})
    assert "[CC SHIM]" in result


def test_answer_tool_call_use_cache_false_skips_cache(tmp_path):
    sim = _make_sim(tmp_path, "T-no-cache", _SAMPLE_EVENTS)
    result = sim.answer_tool_call("read", {"path": "/etc/config.json"}, use_cache=False)
    assert "[CC SHIM]" in result


# ── decision_points ───────────────────────────────────────────────────────────

def test_decision_points_extracts_all(tmp_path):
    sim = _make_sim(tmp_path, "T-dp", _SAMPLE_EVENTS)
    points = sim.decision_points()
    assert len(points) == 3
    assert all(p["decision"] == "tool_selection" for p in points)


def test_decision_points_empty_when_no_decision_field(tmp_path):
    events = [
        {"timestamp": "T", "turn": 1, "decision_point": "", "tool_name": "read",
         "tool_args": {}, "tool_result": "ok", "outcome": "success"},
    ]
    sim = _make_sim(tmp_path, "T-no-dp", events)
    assert sim.decision_points() == []


def test_decision_points_includes_choice_and_outcome(tmp_path):
    sim = _make_sim(tmp_path, "T-dp-fields", _SAMPLE_EVENTS)
    first = sim.decision_points()[0]
    assert first["choice"] == "read"
    assert first["outcome"] == "failure"
    assert first["turn"] == 1


# ── success_rate ──────────────────────────────────────────────────────────────

def test_success_rate_computed_correctly(tmp_path):
    sim = _make_sim(tmp_path, "T-rate", _SAMPLE_EVENTS)
    # 2 success out of 3 → 0.666...
    rate = sim.success_rate()
    assert abs(rate - 2 / 3) < 0.01


def test_success_rate_zero_when_no_events(tmp_path):
    with patch("devices.dicksimnel.simulator._LOGS_ROOT", tmp_path):
        sim = TicketSimulator("T-empty-rate")
    assert sim.success_rate() == 0.0


# ── record_outcome ────────────────────────────────────────────────────────────

def test_record_outcome_updates_event(tmp_path):
    sim = _make_sim(tmp_path, "T-record", _SAMPLE_EVENTS)
    sim.record_outcome(1, "new result", success=True)
    events = list(sim.replay_all())
    turn1 = next(e for e in events if e.turn_num == 1)
    assert turn1.outcome == "success"
    assert turn1.tool_result == "new result"


def test_record_outcome_marks_failure(tmp_path):
    sim = _make_sim(tmp_path, "T-record-fail", _SAMPLE_EVENTS)
    sim.record_outcome(2, "crash", success=False)
    events = list(sim.replay_all())
    turn2 = next(e for e in events if e.turn_num == 2)
    assert turn2.outcome == "failure"


# ── 3 closed datasets (integration — live log files) ─────────────────────────

_INFERENCE_ROOT = (
    Path.home() / ".unseen_university" / "Igor-wild-0001" / "datacenter_logs" / "inference"
)

_CLOSED_TICKETS = ["T-test-closed-ticket", "T-realistic-test", "T-error-pattern-test"]


@pytest.mark.parametrize("ticket_id", _CLOSED_TICKETS)
def test_replay_closed_ticket_completes_without_live_inference(ticket_id):
    """Replay each closed ticket from log files without any live inference."""
    if not (_INFERENCE_ROOT / ticket_id).exists():
        pytest.skip(f"log dir not present: {_INFERENCE_ROOT / ticket_id}")

    sim = TicketSimulator(ticket_id)
    events = list(sim.replay_all())

    assert len(events) > 0, f"Expected events for {ticket_id}"
    assert all(isinstance(e, Event) for e in events)
    # Decision points must be loggable
    points = sim.decision_points()
    assert isinstance(points, list)
    # Success rate must be a valid fraction
    rate = sim.success_rate()
    assert 0.0 <= rate <= 1.0


@pytest.mark.parametrize("ticket_id", _CLOSED_TICKETS)
def test_all_tool_calls_answered_from_cache(ticket_id):
    """Every tool call in a closed ticket's log can be answered from cache."""
    if not (_INFERENCE_ROOT / ticket_id).exists():
        pytest.skip(f"log dir not present: {_INFERENCE_ROOT / ticket_id}")

    sim = TicketSimulator(ticket_id)

    for event in sim.replay_all():
        if not event.tool_name:
            continue
        result = sim.answer_tool_call(
            event.tool_name, event.tool_args or {}, use_cache=True
        )
        # Cache hit → no CC SHIM marker (unless tool_result was originally empty)
        if event.tool_result:
            assert "[CC SHIM]" not in result, (
                f"Expected cache hit for {event.tool_name} in {ticket_id} "
                f"but got CC shim response"
            )


# ── device integration ────────────────────────────────────────────────────────

def test_device_replay_and_analyze_returns_structured_result(tmp_path):
    """DickSimnelDevice.replay_and_analyze returns expected keys."""
    from devices.dicksimnel.device import DickSimnelDevice

    _write_turns(tmp_path / "T-analyze-test", _SAMPLE_EVENTS)

    with patch("devices.dicksimnel.simulator._LOGS_ROOT", tmp_path), \
         patch.object(DickSimnelDevice, "__init__", lambda self: None):
        device = DickSimnelDevice.__new__(DickSimnelDevice)
        result = device.replay_and_analyze("T-analyze-test")

    assert result["ticket_id"] == "T-analyze-test"
    assert result["event_count"] == 3
    assert isinstance(result["decision_points"], list)
    assert 0.0 <= result["success_rate"] <= 1.0
    assert len(result["turns"]) == 3


def test_device_replay_and_analyze_empty_ticket(tmp_path):
    """replay_and_analyze returns zero counts for ticket with no logs."""
    from devices.dicksimnel.device import DickSimnelDevice

    with patch("devices.dicksimnel.simulator._LOGS_ROOT", tmp_path), \
         patch.object(DickSimnelDevice, "__init__", lambda self: None):
        device = DickSimnelDevice.__new__(DickSimnelDevice)
        result = device.replay_and_analyze("T-no-events")

    assert result["event_count"] == 0
    assert result["decision_points"] == []
