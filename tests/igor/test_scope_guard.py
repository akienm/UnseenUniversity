"""
tests/test_scope_guard.py — T-scope-guard-proc unit tests.

Tests _classify_tier() and run_scope_guard() without DB or channel I/O.
The ring write and channel post inside run_scope_guard are wrapped in try/except,
so they fail silently when Cortex/channel_post aren't available in test — that's fine.
We test the classification logic and the escalation decision directly.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub out the lazy imports that run_scope_guard uses internally so they don't
# attempt a real DB connection in tests.
_fake_cortex_mod = MagicMock()
_fake_cortex_mod.Cortex = MagicMock(return_value=MagicMock())
sys.modules.setdefault("wild_igor.igor.memory.cortex", _fake_cortex_mod)

_fake_channel_mod = MagicMock()
sys.modules.setdefault("wild_igor.igor.tools.channel_post", _fake_channel_mod)

from wild_igor.igor.tools.scope_guard import (
    _classify_tier,
    run_scope_guard,
)  # noqa: E402

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
    return {
        "hypothesis": {"file": file, "old_string": "x", "new_string": "y"},
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
    result = run_scope_guard(
        _basket("wild_igor/igor/brainstem/kernel.py", op_type="write")
    )
    assert result.get("pe_status") == "escalated"
    assert "HIGH" in result.get("escalate_reason", "")


def test_scope_guard_high_file_delete_escalates():
    result = run_scope_guard(
        _basket("wild_igor/igor/memory/models.py", op_type="delete")
    )
    assert result.get("pe_status") == "escalated"


def test_scope_guard_high_file_read_passes():
    """Read ops on HIGH files are safe — only writes/deletes escalate."""
    result = run_scope_guard(
        _basket("wild_igor/igor/brainstem/kernel.py", op_type="read")
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
