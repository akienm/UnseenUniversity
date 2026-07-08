#!/usr/bin/env python3
"""validity_sweep — STUB (T-validity-sweep-day-close, red phase).

Importable stub so the proof reverts to an AssertionError, not an ImportError.
Real implementation lands in the next commit.
"""
from __future__ import annotations


def sweep(memory_root=None, *, repo=None, apply=False, sweep_run="manual"):
    return {"checked": 0, "flagged": 0, "unresolvable": 0,
            "flagged_entries": [], "curate_candidates": []}


def format_summary(result):
    return (f"validity sweep: flagged={result['flagged']} "
            f"checked={result['checked']} unresolvable={result['unresolvable']}")
