"""
test_pe_chain_description_cap.py — T-pe-chain-description-cap-too-tight

Regression guards against truncation of ticket descriptions before they
reach the tier.2 reasoning model. The old caps (600 for reasoning steps,
200 for fallback) cut structured fields — Affected files, Design rules,
Scope boundary, Test plan — off the tail of the description because the
/ticket template places them near the bottom. Observed on 2026-04-24 for
T-consult-confidence-threshold-raise (710 chars, plan returned as a
40-char truncated fragment).

The fix raises the caps to 4000 (reasoning) / 2000 (fallback). These
tests pin the values and pin the behavior: full description passes
through when ≤4000 chars, gets cut to exactly 4000 above that.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _pe_chain():
    from wild_igor.igor.tools import pe_chain as pc

    return pc


# ── caps are pinned at 4000 / 2000 ───────────────────────────────────────────


def test_desc_cap_reasoning_is_4000():
    """Reasoning steps (PLAN, SITUATE, HYPOTHESIZE) send up to 4000 chars."""
    pc = _pe_chain()
    assert pc._DESC_CAP_REASONING == 4000, (
        "Dropping below 4000 starts re-cutting ticket template tails — "
        "T-pe-chain-description-cap-too-tight"
    )


def test_desc_cap_fallback_is_2000():
    """PLAN fallback (tier.2 unavailable) keeps up to 2000 chars."""
    pc = _pe_chain()
    assert pc._DESC_CAP_FALLBACK == 2000


# ── slice call sites use the constants, not raw 600/400/200 ──────────────────


def test_pe_chain_source_uses_desc_cap_constants():
    """Regression guard: the three prompt-building sites that slice
    description must reference the constants, not hardcoded 600/400/200.
    If someone later inlines a magic number, this test catches it.
    """
    import inspect
    from wild_igor.igor.tools import pe_chain as pc

    src = inspect.getsource(pc)

    # Must use the named constants at the reasoning sites
    assert "description[:_DESC_CAP_REASONING]" in src, (
        "pe_plan/pe_situate/pe_hypothesize must use _DESC_CAP_REASONING, "
        "not a hardcoded slice"
    )
    assert "description[:_DESC_CAP_FALLBACK]" in src

    # Hardcoded slices at the old values must NOT reappear on description
    assert "description[:600]" not in src
    assert "description[:400]" not in src
    assert "description[:200]" not in src


# ── slicing behavior: short descriptions pass through, long ones truncate ────


def test_short_description_passes_through_unchanged():
    """A 710-char description (the failing case from 2026-04-24) must
    flow through without being cut."""
    pc = _pe_chain()
    desc = "x" * 710
    truncated = desc[: pc._DESC_CAP_REASONING]
    assert truncated == desc
    assert len(truncated) == 710


def test_long_description_cut_at_4000_not_600():
    """A 5000-char description is cut to 4000, not 600."""
    pc = _pe_chain()
    desc = "y" * 5000
    truncated = desc[: pc._DESC_CAP_REASONING]
    assert len(truncated) == 4000
    assert len(truncated) != 600  # explicit — the old cap is gone
