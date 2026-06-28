"""Tests for devices/librarian/trust.py."""

from __future__ import annotations

import pytest

from unseen_university.devices.librarian.trust import (
    TIER_DESCRIPTIONS,
    derive_trust_tier,
    passes_min_tier,
)

# ── derive_trust_tier ─────────────────────────────────────────────────────────


def test_tier_0_for_none():
    assert derive_trust_tier(None) == 0


def test_tier_0_for_empty_string():
    assert derive_trust_tier("") == 0


def test_tier_1_for_cc_sprint():
    assert derive_trust_tier("cc/sprint") == 1


def test_tier_1_for_cc_sprint_with_suffix():
    assert derive_trust_tier("cc/sprint-2026-05-30") == 1


def test_tier_1_for_cc_dash_sprint():
    assert derive_trust_tier("cc-sprint") == 1


def test_tier_2_for_igor_ne_checkpoint():
    assert derive_trust_tier("igor/ne-checkpoint") == 2


def test_tier_2_for_igor_checkpoint():
    assert derive_trust_tier("igor/checkpoint") == 2


def test_tier_2_for_igor_ne_checkpoint_dash_form():
    assert derive_trust_tier("igor-ne-checkpoint") == 2


def test_tier_3_for_librarian_recall():
    assert derive_trust_tier("librarian-recall") == 3


def test_tier_3_for_arbitrary_agent():
    assert derive_trust_tier("some-autonomous-device") == 3


def test_tier_3_for_igor_without_checkpoint():
    # "igor/" alone (no checkpoint suffix) → autonomous → tier_3
    assert derive_trust_tier("igor/ne") == 3


def test_tier_descriptions_covers_all_tiers():
    for tier in (0, 1, 2, 3):
        assert tier in TIER_DESCRIPTIONS


# ── passes_min_tier ───────────────────────────────────────────────────────────


def test_no_filter_always_passes():
    for tier in (0, 1, 2, 3):
        assert passes_min_tier(tier, None) is True


def test_min_tier_2_accepts_tier_1():
    assert passes_min_tier(1, 2) is True


def test_min_tier_2_accepts_tier_2():
    assert passes_min_tier(2, 2) is True


def test_min_tier_2_rejects_tier_3():
    assert passes_min_tier(3, 2) is False


def test_min_tier_2_rejects_tier_0():
    assert passes_min_tier(0, 2) is False


def test_min_tier_1_accepts_only_tier_1():
    assert passes_min_tier(1, 1) is True
    assert passes_min_tier(2, 1) is False
    assert passes_min_tier(3, 1) is False
    assert passes_min_tier(0, 1) is False


def test_min_tier_3_accepts_tier_1_2_3():
    assert passes_min_tier(1, 3) is True
    assert passes_min_tier(2, 3) is True
    assert passes_min_tier(3, 3) is True
    assert passes_min_tier(0, 3) is False  # tier_0 always fails active filter
