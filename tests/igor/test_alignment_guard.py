"""
Tests for devices/igor/tools/alignment_guard.py — T-long-horizon-alignment-guard.

Verifies:
  - record_ne_cycle increments same-goal counter
  - counter resets on goal change
  - reset_interaction clears the counter
  - check_and_alert fires escalation at threshold and resets counter
  - extract_goal_id_from_twm parses facia_id from TWM rows
"""

import importlib
from unittest.mock import MagicMock, patch


def _reload_guard():
    """Reload module to reset module-level state between tests."""
    import unseen_university.devices.igor.tools.alignment_guard as ag

    ag._same_goal_cycles = 0
    ag._current_goal_id = None
    return ag


def test_same_goal_increments_counter():
    ag = _reload_guard()
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle("G-001")
    assert ag._same_goal_cycles == 3


def test_goal_change_resets_counter():
    ag = _reload_guard()
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle("G-002")
    # New goal → count resets to 1
    assert ag._same_goal_cycles == 1
    assert ag._current_goal_id == "G-002"


def test_none_goal_id_conservative():
    ag = _reload_guard()
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle(None)  # unknown goal_id treated as same
    assert ag._same_goal_cycles == 2


def test_reset_interaction_clears_counter():
    ag = _reload_guard()
    ag.record_ne_cycle("G-001")
    ag.record_ne_cycle("G-001")
    ag.reset_interaction()
    assert ag._same_goal_cycles == 0
    assert ag._current_goal_id is None


_ESCALATE_PATH = "unseen_university.devices.igor.cognition.escalate.escalate_to_channel"


def test_check_and_alert_fires_at_threshold(monkeypatch):
    ag = _reload_guard()
    alerts_fired = []

    def fake_escalate(msg, dedup_key=None, watch_condition=None):
        alerts_fired.append(msg)

    monkeypatch.setenv("IGOR_ALIGNMENT_GUARD_THRESHOLD", "5")
    with patch(_ESCALATE_PATH, fake_escalate):
        for _ in range(4):
            ag.record_ne_cycle("G-001")
        assert ag.check_and_alert() is False  # not yet at threshold

        ag.record_ne_cycle("G-001")  # cycle 5 → threshold crossed
        fired = ag.check_and_alert()

    assert fired is True
    assert len(alerts_fired) == 1
    assert "5 consecutive NE cycles" in alerts_fired[0]
    assert "G-001" in alerts_fired[0]
    # Counter resets after alert
    assert ag._same_goal_cycles == 0


def test_check_and_alert_resets_prevents_spam(monkeypatch):
    ag = _reload_guard()
    alerts_fired = []

    def fake_escalate(msg, **_):
        alerts_fired.append(msg)

    monkeypatch.setenv("IGOR_ALIGNMENT_GUARD_THRESHOLD", "3")
    with patch(_ESCALATE_PATH, fake_escalate):
        for _ in range(3):
            ag.record_ne_cycle("G-001")
        ag.check_and_alert()  # fires at 3
        # Immediately check again — counter was reset, should not fire
        result = ag.check_and_alert()

    assert len(alerts_fired) == 1
    assert result is False


def test_extract_goal_id_from_twm_found():
    ag = _reload_guard()
    rows = [
        {"content_csb": "HEARTBEAT|ts=12345"},
        {
            "content_csb": "ACTIVE_GOAL_SURFACED|facia_id=G-priority-reading|type=goal_strategic|name=Read more"
        },
    ]
    assert ag.extract_goal_id_from_twm(rows) == "G-priority-reading"


def test_extract_goal_id_from_twm_not_found():
    ag = _reload_guard()
    rows = [
        {"content_csb": "HEARTBEAT|ts=12345"},
        {"content_csb": "LTM_FORCE|EPISODIC"},
    ]
    assert ag.extract_goal_id_from_twm(rows) is None


def test_extract_goal_id_from_twm_empty():
    ag = _reload_guard()
    assert ag.extract_goal_id_from_twm([]) is None
