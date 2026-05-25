"""test_ne_invariant_guard.py — T-cc-walk-20

Verifies the NE stew-salience invariant guard:
  stew_salience must be > ne_force_run_threshold

A misconfigured inversion silently breaks the guarantee that stew
observations trigger NE force-runs.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def _make_cortex():
    return MagicMock()


class TestNEInvariantGuard:
    def test_default_params_ok(self):
        """Default thresholds satisfy the invariant — no exception."""
        from devices.igor.cognition.narrative_engine import NarrativeEngine

        ne = NarrativeEngine(_make_cortex())
        assert ne is not None

    def test_inverted_thresholds_raise(self):
        """stew_salience <= force_run_threshold must raise ValueError."""
        from devices.igor.cognition.narrative_engine import NarrativeEngine

        with pytest.raises(ValueError, match="NE invariant violated"):
            NarrativeEngine(_make_cortex(), stew_salience=0.5, force_run_threshold=0.6)

    def test_equal_thresholds_raise(self):
        """Equal values also violate the invariant (stew obs would only tie, not exceed)."""
        from devices.igor.cognition.narrative_engine import NarrativeEngine

        with pytest.raises(ValueError, match="NE invariant violated"):
            NarrativeEngine(_make_cortex(), stew_salience=0.6, force_run_threshold=0.6)

    def test_error_message_names_both_values(self):
        """Error message should mention both threshold values for fast diagnosis."""
        from devices.igor.cognition.narrative_engine import NarrativeEngine

        with pytest.raises(ValueError) as exc_info:
            NarrativeEngine(_make_cortex(), stew_salience=0.5, force_run_threshold=0.6)
        msg = str(exc_info.value)
        assert "0.5" in msg
        assert "0.6" in msg
