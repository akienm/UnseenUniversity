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


def _no_ticket_writes(device):
    # Single-writer (D-granny-sole-ticket-writer): the builder must make ZERO ticket
    # mutations — only Granny writes. _build_report may READ (show) but never
    # close/setstatus/append-note.
    verbs = [c[0][0] for c in device._run_queue_cmd.call_args_list]
    assert not ({"close", "setstatus", "append-note"} & set(verbs)), f"builder wrote: {verbs}"


def test_passed_gate_returns_done_artifact_no_writes(device):
    # A branch-builder can't emit a HEAD-valid proof — it REPORTS a done artifact naming
    # the missing proof-lever (merge-time proof); GRANNY closes shipped-unproven.
    art = device._build_report("T-x", _passing())
    assert art["outcome"] == "done"
    assert art["branch"] == "aider/T-x-1"
    assert "missing_lever" in art and art["missing_lever"]
    _no_ticket_writes(device)


def test_zero_edits_returns_escalated_artifact(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=False, gate_passed=False)
    art = device._build_report("T-x", r)
    assert art["outcome"] == "escalated"
    assert "0 edits" in art["reason"]
    _no_ticket_writes(device)


def test_red_tests_escalate(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["shop/models.py"], tests_green=False, gate_passed=False)
    assert device._build_report("T-x", r)["outcome"] == "escalated"
    _no_ticket_writes(device)


def test_scope_blocked_escalates(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["tests/test_x.py"], tests_green=True,
                    scope_blocked=True, scope_reasons=["edited test file: tests/test_x.py"],
                    gate_passed=False)
    assert device._build_report("T-x", r)["outcome"] == "escalated"
    _no_ticket_writes(device)


def test_no_test_target_escalates(device):
    r = AiderResult(ticket_id="T-x", model="m", branch="b", edited=True,
                    changed_files=["shop/models.py"], tests_green=None, gate_passed=False)
    assert device._build_report("T-x", r)["outcome"] == "escalated"


def test_none_result_escalates(device):
    assert device._build_report("T-x", None)["outcome"] == "escalated"


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
