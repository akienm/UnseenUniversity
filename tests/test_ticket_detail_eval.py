"""Tests for devlab/claudecode/ticket_detail_eval.py (T-opus-ticket-eval).

Covers detail_score, spearman_r, approx_p_value, and the direction-aware
recommendation logic.
"""

from __future__ import annotations

import math

import pytest

from devlab.claudecode.ticket_detail_eval import (
    _has_concrete_affected_files,
    _has_real_criteria,
    approx_p_value,
    detail_score,
    spearman_r,
)


# ── detail_score helpers ──────────────────────────────────────────────────────

def test_has_concrete_files_present():
    desc = "**Affected files:** unseen_university/shim.py, devices/foo/bar.py"
    assert _has_concrete_affected_files(desc) is True


def test_has_concrete_files_tbd():
    desc = "**Affected files:** TBD"
    assert _has_concrete_affected_files(desc) is False


def test_has_concrete_files_absent():
    desc = "No affected files section here."
    assert _has_concrete_affected_files(desc) is False


def test_has_real_criteria_present():
    desc = "**Completion criteria:** Script outputs per-ticket detail and build cost rows."
    assert _has_real_criteria(desc) is True


def test_has_real_criteria_absent():
    desc = "Just a description, no criteria section."
    assert _has_real_criteria(desc) is False


def test_has_real_criteria_empty():
    desc = "**Completion criteria:**\n\n**Design rules:** something"
    assert _has_real_criteria(desc) is False


def test_detail_score_full():
    desc = (
        "A description of reasonable length. " * 5
        + "**Affected files:** devices/foo/main.py\n"
        + "**Completion criteria:** The output contains a yes/no recommendation.\n"
    )
    ds = detail_score(desc)
    assert ds["concrete_files"] is True
    assert ds["real_criteria"] is True
    assert ds["score"] > 20  # chars/50 + 10 + 10


def test_detail_score_no_sections():
    desc = "Short."
    ds = detail_score(desc)
    assert ds["concrete_files"] is False
    assert ds["real_criteria"] is False
    assert ds["score"] < 5


# ── spearman_r ────────────────────────────────────────────────────────────────

def test_spearman_perfect_positive():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert abs(spearman_r(x, x) - 1.0) < 1e-6


def test_spearman_perfect_negative():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [5.0, 4.0, 3.0, 2.0, 1.0]
    assert abs(spearman_r(x, y) - (-1.0)) < 1e-6


def test_spearman_zero_variance_returns_nan():
    x = [1.0, 1.0, 1.0]
    y = [1.0, 2.0, 3.0]
    result = spearman_r(x, y)
    assert math.isnan(result)


def test_spearman_uncorrelated():
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0]
    r = spearman_r(x, y)
    assert -1.0 <= r <= 1.0


def test_spearman_too_few_points():
    result = spearman_r([1.0, 2.0], [3.0, 4.0])
    assert math.isnan(result)


# ── approx_p_value ────────────────────────────────────────────────────────────

def test_p_value_strong_correlation_is_low():
    # r=0.9, n=20 → t ≈ 8.6 → p ≈ 0
    p = approx_p_value(0.9, 20)
    assert p < 0.01


def test_p_value_zero_correlation_is_high():
    p = approx_p_value(0.0, 30)
    assert p > 0.9


def test_p_value_nan_r():
    result = approx_p_value(float("nan"), 30)
    assert math.isnan(result)


def test_p_value_moderate_correlation():
    # r=0.362, n=34 — close to p ≈ 0.035 from exact t-table
    p = approx_p_value(0.362, 34)
    assert 0.01 < p < 0.10  # in the right ballpark


def test_p_value_too_few_points():
    result = approx_p_value(0.5, 2)
    assert math.isnan(result)
