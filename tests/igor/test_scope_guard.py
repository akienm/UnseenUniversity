"""
tests/test_scope_guard.py — T-scope-guard-proc unit tests.

Tests _classify_tier() and run_scope_guard() without DB or channel I/O.
"""

from unittest.mock import MagicMock, patch

import pytest

from wild_igor.igor.tools.scope_guard import (
    _classify_tier,
    run_scope_guard,
)  # noqa: E402


@pytest.fixture(autouse=True)
def _no_channel_io():
    """Block channel writes for every test in this module.

    Without this, the MEDIUM-inertia test posts to the real channel on every pytest
    run — writing to production infra.channel_messages. HIGH-inertia paths call
    _pe_escalate via a lazy import that fails silently; only the MEDIUM path reaches
    real Postgres.
    """
    with patch("wild_igor.igor.tools.channel_post.post_to_channel"):
        yield


# ── _classify_tier ────────────────────────────────────────────────────────────


def test_classify_tier_high_brainstem():
    assert _classify_tier("wild_igor/igor/brainstem/some_module.py") == "HIGH"


def test_classify_tier_high_models():
    assert _classify_tier("wild_igor/igor/memory/models.py") == "HIGH"


def test_classify_tier_high_base():
    assert _classify_tier("wild_igor/igor/cognition/reasoners/base.py") == "HIGH"


def test_classify_tier_medium_cognition():
    assert _classify_tier("wild_igor/igor/cognition/thalamus.py") == "MEDIUM"


def test_classify_tier_medium_cortex():
    assert _classify_tier("wild_igor/igor/memory/cortex.py") == "MEDIUM"


def test_classify_tier_medium_main():
    assert _classify_tier("wild_igor/igor/main.py") == "MEDIUM"


def test_classify_tier_low_tools():
    assert _classify_tier("wild_igor/igor/tools/pe_chain.py") == "LOW"


def test_classify_tier_low_unknown():
    assert _classify_tier("some/random/file.py") == "LOW"


def test_classify_tier_absolute_path_brainstem():
    import os

    abs_path = os.path.expanduser("~/TheIgors/wild_igor/igor/brainstem/core.py")
    assert _classify_tier(abs_path) == "HIGH"


# ── run_scope_guard ───────────────────────────────────────────────────────────


def _basket(file: str, op_type: str = "write") -> dict:
    # ticket_id must be a non-"unknown" sentinel that doesn't exist in the queue
    # so _pe_escalate skips GOAL recovery (which would otherwise grab the active
    # GOAL and cross-check the test file against the active ticket's description,
    # causing flakes when run in the full suite alongside Igor's live DB).
    return {
        "ticket_id": "T-test-scope-guard-sentinel",
        "hypothesis": {"file": file, "old_string": "x", "new_string": "y"},
        "hypotheses": [{"file": file, "old_string": "x", "new_string": "y"}],
        "op_type": op_type,
    }


def test_scope_guard_low_file_passes():
    result = run_scope_guard(_basket("wild_igor/igor/tools/some_tool.py"))
    assert "escalate_reason" not in result
    assert result.get("pe_status") != "escalated"


def test_scope_guard_medium_file_passes():
    result = run_scope_guard(_basket("wild_igor/igor/cognition/thalamus.py"))
    assert "escalate_reason" not in result


def test_scope_guard_high_file_write_escalates():
    # Use a real HIGH-inertia file — kernel.py doesn't exist, and the
    # T-escalate-validates-file-exists guard rewrites reasons for nonexistent
    # target_files to "hallucinated file: ..." (block, not propose).
    result = run_scope_guard(
        _basket("wild_igor/igor/brainstem/core_patterns.py", op_type="write")
    )
    # _pe_escalate() sets escalate_reason (not pe_status) and closes the goal
    assert "HIGH" in result.get("escalate_reason", "")


def test_scope_guard_high_file_delete_escalates():
    result = run_scope_guard(
        _basket("wild_igor/igor/memory/models.py", op_type="delete")
    )
    assert result.get("escalate_reason")


def test_scope_guard_high_file_read_passes():
    """Read ops on HIGH files are safe — only writes/deletes escalate."""
    result = run_scope_guard(
        _basket("wild_igor/igor/brainstem/core_patterns.py", op_type="read")
    )
    assert result.get("pe_status") != "escalated"
    assert "escalate_reason" not in result


def test_scope_guard_no_hypothesis_skips():
    basket = {"op_type": "write"}
    result = run_scope_guard(basket)
    assert result == basket  # unchanged, no side effects


def test_scope_guard_hypothesis_error_skips():
    basket = {
        "hypothesis": {"file": "wild_igor/igor/brainstem/x.py"},
        "hypothesis_error": "validation failed: missing old_string",
    }
    result = run_scope_guard(basket)
    assert "escalate_reason" not in result


# ── inertia_map parity ────────────────────────────────────────────────────────


def test_classify_tier_matches_inertia_map():
    """_classify_tier must agree with inertia_map.bucket_of on canonical paths."""
    from wild_igor.igor.tools.inertia_map import bucket_of

    probes = [
        ("wild_igor/igor/brainstem/kernel.py", "HIGH"),
        ("wild_igor/igor/memory/models.py", "HIGH"),
        ("wild_igor/igor/cognition/reasoners/base.py", "HIGH"),
        ("wild_igor/igor/cognition/thalamus.py", "MEDIUM"),
        ("wild_igor/igor/memory/cortex.py", "MEDIUM"),
        ("wild_igor/igor/main.py", "MEDIUM"),
        ("wild_igor/igor/tools/pe_chain.py", "LOW"),
        ("some/random/file.py", "LOW"),
    ]
    for path, expected in probes:
        assert _classify_tier(path) == expected, f"_classify_tier({path!r})"
        assert bucket_of(path) == expected, f"bucket_of({path!r})"
