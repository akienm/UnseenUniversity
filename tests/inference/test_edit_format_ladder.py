"""
THE PROOF NODE for the edit-format ladder (T-aider-port-editformat-conformance).

Hypothesis: a model that fails the strict block (SEARCH/REPLACE) dialect falls back to the simpler
whole-file dialect and SUCCEEDS on the same intent. Driven through the stable public surface
ArchitectEditorFlow (imports no P8-only symbol) so the reverted (pre-ladder) run imports cleanly
and reaches an authentic AssertionError: without the ladder, block-0-applied → escalate and the
file is never written; with it, the whole-file fallback writes the file.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from unseen_university.devices.inference.architect_editor import ArchitectEditorFlow


def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = r.output_tokens = 10
    r.cost_estimate = 0.0
    return r


class _LadderDev:
    """Architect → plan; block editor → prose that yields NO edits; whole-file fallback → a valid
    whole-file the harness can apply. Keyed on the prompt, so it works with and without the ladder."""

    def dispatch(self, req):
        if req.tools is not None:  # architect
            return _resp(json.dumps({"status": "done", "result": "Edit target.py to 42.",
                                     "error_class": None, "error_number": None}))
        if "ENTIRE new contents" in (req.system or ""):  # whole-file fallback dialect
            return _resp("target.py\n```\nvalue = 42\n```\n")
        # block dialect: the weak model can't produce a valid SEARCH/REPLACE block → 0 edits
        return _resp("Sorry, I am not able to produce a SEARCH/REPLACE block for this.")


def test_block_fails_wholefile_ladder_succeeds(tmp_path):
    (tmp_path / "target.py").write_text("value = 1\n")
    result = ArchitectEditorFlow(block_editor_enabled=True, inference_device=_LadderDev()).run(
        system_prompt="", initial_message="set value to 42 in target.py",
        ticket_id="T-ladder", cwd=tmp_path,
    )
    assert "value = 42" in (tmp_path / "target.py").read_text(), (
        "block format applied nothing; the whole-file ladder must fall back and write the file"
    )
    assert result.outcome == "done"
    assert (result.envelope or {}).get("edit_format") == "wholefile", (
        f"the DONE envelope must record which dialect actually worked; got {result.envelope}"
    )
