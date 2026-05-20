"""Tests for pe_chain TypedDict phase contracts (T-pe-chain-typed-contracts).

Demonstrates the independence property: a single phase can be constructed
from its typed input directly and tested without running the full pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from wild_igor.igor.tools import pe_chain
from wild_igor.igor.tools.pe_chain import (
    PeHypothesizeInput,
    PeHypothesizeOutput,
)


class TestPeHypothesizeIndependence:
    """pe_hypothesize receives a typed input and produces a typed output.

    No upstream phases run — the input is constructed directly from the
    TypedDict contract. This is the independence property the refactor targets.
    """

    def _llm_response(self, edits: list) -> str:
        return json.dumps({"edits": edits})

    def test_produces_hypotheses_from_typed_input(self, tmp_path):
        """Construct PeHypothesizeInput directly; assert output keys present."""
        target = tmp_path / "widget.py"
        target.write_text("def old_fn():\n    pass\n")

        basket: PeHypothesizeInput = {
            "ticket_description": "Rename old_fn to new_fn in widget.py",
            "actual": "def old_fn():\n    pass\n",
            "plan_files": [str(target)],
        }

        llm_out = self._llm_response(
            [
                {
                    "file": str(target),
                    "old_string": "def old_fn():",
                    "new_string": "def new_fn():",
                }
            ]
        )

        with patch.object(pe_chain, "_call_tier2", return_value=llm_out):
            result: PeHypothesizeOutput = pe_chain.pe_hypothesize(basket)

        assert "hypotheses" in result
        assert "hypothesis_raw" in result
        assert isinstance(result["hypotheses"], list)

    def test_no_description_sets_error(self):
        """Missing ticket_description → error key set, no tier2 call."""
        basket: PeHypothesizeInput = {}

        with patch.object(pe_chain, "_call_tier2") as mock_tier2:
            result = pe_chain.pe_hypothesize(basket)

        mock_tier2.assert_not_called()
        assert result.get("error")

    def test_empty_actual_no_new_files_produces_ungrounded_error(self):
        """No actual and no new_files → hypothesis_error, empty hypotheses."""
        basket: PeHypothesizeInput = {
            "ticket_description": "Add a widget",
            "actual": "",
            "plan_files": [],
        }

        with patch.object(pe_chain, "_call_tier2") as mock_tier2:
            result = pe_chain.pe_hypothesize(basket)

        mock_tier2.assert_not_called()
        assert result.get("hypotheses") == []
        assert result.get("hypothesis_error")

    def test_approved_plan_bypasses_llm(self, tmp_path):
        """D331 approved_plan path: JSON edits used directly, no tier2 call."""
        target = tmp_path / "foo.py"
        target.write_text("x = 1\n")

        approved = json.dumps(
            {
                "edits": [
                    {"file": str(target), "old_string": "x = 1", "new_string": "x = 2"}
                ]
            }
        )
        basket: PeHypothesizeInput = {
            "ticket_description": "Update x",
            "actual": "x = 1\n",
            "plan_files": [str(target)],
            "approved_plan": approved,
        }

        with patch.object(pe_chain, "_call_tier2") as mock_tier2:
            result = pe_chain.pe_hypothesize(basket)

        mock_tier2.assert_not_called()
        assert len(result.get("hypotheses", [])) == 1
        assert result["hypotheses"][0]["new_string"] == "x = 2"

    def test_typed_input_keys_are_sufficient(self, tmp_path):
        """PeHypothesizeInput keys alone are sufficient to run the phase.

        No 'ticket_id', 'attempt_count', 'goal_id', or any other upstream
        key is needed — demonstrating phase independence.
        """
        target = tmp_path / "bar.py"
        target.write_text("CONST = 'old'\n")

        basket: PeHypothesizeInput = {
            "ticket_description": "Change CONST to 'new'",
            "actual": "CONST = 'old'\n",
            "plan_files": [str(target)],
        }
        # Verify no upstream keys are present
        assert "ticket_id" not in basket
        assert "attempt_count" not in basket
        assert "goal_id" not in basket

        llm_out = self._llm_response(
            [
                {
                    "file": str(target),
                    "old_string": "CONST = 'old'",
                    "new_string": "CONST = 'new'",
                }
            ]
        )
        with patch.object(pe_chain, "_call_tier2", return_value=llm_out):
            result = pe_chain.pe_hypothesize(basket)

        assert result.get("hypotheses") is not None
        assert not result.get("error")
