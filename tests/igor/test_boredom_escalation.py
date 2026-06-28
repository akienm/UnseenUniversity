"""
test_boredom_escalation.py — T-boredom-llm-escalation (#447)

Tests for cascade escalation from boredom idle loop.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.tools.boredom_idle import _try_cascade_escalation  # noqa: E402


class TestCascadeEscalation:
    def test_returns_none_on_exception(self):
        """Cascade escalation swallows errors and returns None."""
        with patch(
            "unseen_university.devices.igor.memory.cortex.Cortex",
            side_effect=RuntimeError("db down"),
        ):
            result = _try_cascade_escalation()
        assert result is None

    def test_returns_none_when_no_cortex(self):
        """Even if imports fail, returns None gracefully."""
        with patch.dict("sys.modules", {"unseen_university.devices.igor.memory.cortex": None}):
            result = _try_cascade_escalation()
        assert result is None

    def test_function_exists_and_callable(self):
        assert callable(_try_cascade_escalation)

    def test_returns_string_or_none(self):
        result = _try_cascade_escalation()
        assert result is None or isinstance(result, str)
