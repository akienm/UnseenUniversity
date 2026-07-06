"""Proof-on-close for AiderDevice: only a passed gate closes; everything else escalates.

`test_passed_gate_closes` + the four `test_*_escalates` cases pin the routing a
hollow builder would get wrong. Also pins the ticket field parsers that feed the
runner (affected files, test targets).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from unseen_university.devices.aider.device import AiderDevice
from unseen_university.devices.aider.runner import AiderResult


@pytest.fixture
def device():
    d = AiderDevice()
    d._run_queue_cmd = MagicMock(return_value={"ok": True})
    d._channel_event = MagicMock()
    return d


def _passing():
    return AiderResult(ticket_id="T-x", model="qwen3-coder:30b", branch="aider/T-x-1",
                       edited=True, changed_files=["shop/models.py"], tests_green=True,
                       scope_blocked=False, gate_passed=True, wall_s=20.0, workdir="/w")


def test_passed_gate_closes(device):
    outcome = device._post_result("T-x", _passing())
    assert outcome == "done"
    verbs = [c[0][0] for c in device._run_queue_cmd.call_args_list]
    assert "close" in verbs
    assert "setstatus" not in verbs  # not escalated


def test_zero_edits_escalates_never_closes(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=False, gate_passed=False)
    outcome = device._post_result("T-x", r)
    assert outcome == "escalated"
    verbs = [c[0][0] for c in device._run_queue_cmd.call_args_list]
    assert "close" not in verbs
    assert ("setstatus", "T-x", "escalated") in [tuple(c[0]) for c in device._run_queue_cmd.call_args_list]


def test_red_tests_escalate(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["shop/models.py"], tests_green=False, gate_passed=False)
    assert device._post_result("T-x", r) == "escalated"
    assert "close" not in [c[0][0] for c in device._run_queue_cmd.call_args_list]


def test_scope_blocked_escalates(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["tests/test_x.py"], tests_green=True,
                    scope_blocked=True, scope_reasons=["edited test file: tests/test_x.py"],
                    gate_passed=False)
    assert device._post_result("T-x", r) == "escalated"
    assert "close" not in [c[0][0] for c in device._run_queue_cmd.call_args_list]


def test_no_test_target_escalates(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["shop/models.py"], tests_green=None, gate_passed=False)
    assert device._post_result("T-x", r) == "escalated"


def test_none_result_escalates(device):
    assert device._post_result("T-x", None) == "escalated"


def test_gate_failure_reason_maps_each_mode():
    r0 = AiderResult(ticket_id="t", model="m", branch="b", edited=False)
    assert "0 edits" in AiderDevice._gate_failure_reason(r0)
    r1 = AiderResult(ticket_id="t", model="m", branch="b", edited=True, scope_blocked=True,
                     scope_reasons=["x"])
    assert "diff-scope" in AiderDevice._gate_failure_reason(r1)
    r2 = AiderResult(ticket_id="t", model="m", branch="b", edited=True, tests_green=None)
    assert "no test target" in AiderDevice._gate_failure_reason(r2)
    r3 = AiderResult(ticket_id="t", model="m", branch="b", edited=True, tests_green=False)
    assert "red" in AiderDevice._gate_failure_reason(r3)


# ── Ticket field parsers ──────────────────────────────────────────────────────

def test_parse_affected_extracts_source_drops_tests():
    desc = "**Affected files:** shop/models.py, shop/service.py, tests/test_x.py\n**Design rules:** none"
    assert AiderDevice._parse_affected(desc) == ["shop/models.py", "shop/service.py"]


def test_parse_affected_none_when_absent():
    assert AiderDevice._parse_affected("no such section") == []


def test_parse_test_targets_from_test_plan():
    desc = "**Test plan:** red->green on tests/aider/test_diff_scope_gate.py then stop"
    assert "tests/aider/test_diff_scope_gate.py" in AiderDevice._parse_test_targets(desc)
