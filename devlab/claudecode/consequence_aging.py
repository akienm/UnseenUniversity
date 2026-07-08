#!/usr/bin/env python3
"""consequence_aging — STUB (T-consequence-ticket-aging-alarm, red phase).

Importable stub so the proof reverts to an AssertionError, not an ImportError.
Real implementation lands in the next commit.
"""
from __future__ import annotations

ESCALATE_AGE_DAYS = 7


def overdue_consequences(tasks, today=None):
    return []


def format_summary(overdue):
    return "consequence overdue: 0"
