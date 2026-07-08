"""Proof for T-consequence-ticket-aging-alarm (gate-attack G7).

A consequence gate mandates the ticket exists but nothing makes it FIRE — many
T-consequence-* tickets sat open past their gate dates. This checker surfaces
the overdue set at day-close. Hermetic: a seeded overdue consequence appears with
the correct age; a closed one, a not-yet-due one, and one still blocked by an open
predecessor do NOT. RED (AssertionError / AttributeError-free) on the pre-checker
tree via the second test's presence assertion; the module is pure logic over an
injected task list and a pinned `today`, so no store or clock dependency.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import consequence_aging as ca  # noqa: E402

_TODAY = date(2026, 7, 10)


def _t(tid, status="sprint", gate=None):
    return {"id": tid, "status": status, "gate": gate, "title": tid}


def test_overdue_consequence_is_surfaced_with_correct_age():
    tasks = [_t("T-consequence-foo", gate="2026-07-01")]
    overdue = ca.overdue_consequences(tasks, today=_TODAY)
    assert len(overdue) == 1
    assert overdue[0]["id"] == "T-consequence-foo"
    assert overdue[0]["age_days"] == 9  # 2026-07-10 - 2026-07-01
    assert ca.format_summary(overdue) == \
        "consequence overdue: 1 (oldest: T-consequence-foo, age 9d)"


def test_closed_and_not_yet_due_and_non_consequence_are_excluded():
    tasks = [
        _t("T-consequence-done", status="closed", gate="2026-07-01"),   # terminal
        _t("T-consequence-future", gate="2026-12-31"),                  # not due
        _t("T-consequence-today", gate="2026-07-10"),                   # due today, not overdue
        _t("T-plain-overdue", gate="2026-07-01"),                       # not a consequence id
    ]
    assert ca.overdue_consequences(tasks, today=_TODAY) == []
    assert ca.format_summary([]) == "consequence overdue: 0"


def test_consequence_still_blocked_by_open_predecessor_is_not_overdue():
    # Date elapsed but a referenced predecessor is still open -> legitimately
    # blocked, not overdue (gate not fully clear).
    tasks = [
        _t("T-consequence-bar", gate="2026-07-01 T-blocker"),
        _t("T-blocker", status="sprint"),
    ]
    assert ca.overdue_consequences(tasks, today=_TODAY) == []


def test_escalation_threshold_marks_the_seven_day_old_ones():
    tasks = [
        _t("T-consequence-old", gate="2026-07-01"),   # 9d -> escalate
        _t("T-consequence-fresh", gate="2026-07-08"),  # 2d -> no
    ]
    overdue = ca.overdue_consequences(tasks, today=_TODAY)
    ages = {e["id"]: e["age_days"] for e in overdue}
    assert ages["T-consequence-old"] >= ca.ESCALATE_AGE_DAYS
    assert ages["T-consequence-fresh"] < ca.ESCALATE_AGE_DAYS
    # sorted most-overdue first
    assert overdue[0]["id"] == "T-consequence-old"
