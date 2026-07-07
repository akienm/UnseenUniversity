"""
THE PROOF NODE for T-aider-port-editor-block-contract (the hypothesis, not port fidelity).

Hypothesis: a multi-file coding attempt that the DS editor left with 0 edits (F-B: never chose
to call Edit) now PRODUCES edits under the block-editor flag. Driven through the PUBLIC surface
`CodingDomain.run(ticket, cwd=...)` with a mocked InferenceDevice.

The dispatch mock keys on the request, NOT a call counter, so it behaves for both paths:
  - tools is None            → BLOCK EDITOR phase → return SEARCH/REPLACE blocks (the response
                               IS the edits) targeting BOTH files.
  - "Edit" offered           → tool-loop EDITOR (the reverted default) → emit PROSE, never an
                               Edit call — the exact F-B failure (0 edits).
  - else (Read offered)      → ARCHITECT phase → return a done-envelope carrying the plan.

GREEN (flag on at HEAD): architect plans → block editor emits blocks → block_apply writes BOTH
files → both markers flip. RED (impl reverted → the flag is ignored → tool-loop editor runs):
Edit is offered, the mock emits prose, no file is written → the markers never flip →
AssertionError.

Revert-safety: imports ONLY stable symbols (CodingDomain, InferenceDevice) and names no
impl-only symbol (block_apply, _run_block_editor, block_editor_enabled as a ctor arg), so the
reverted run imports cleanly and reaches the assertion rather than dying at collection
(proof_emitter_gotchas: red must be AssertionError, not ImportError).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.domains.coding import CodingDomain


def _resp(*, text: str = "", tool_calls=None) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tool_calls = tool_calls
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = 10
    r.output_tokens = 10
    r.cost_estimate = 0.0
    r.model = "mock-model"
    return r


_BLOCKS = (
    "subject_a.py\n"
    "<<<<<<< SEARCH\n"
    "flag = 'OLD_A'\n"
    "=======\n"
    "flag = 'NEW_A'\n"
    ">>>>>>> REPLACE\n"
    "\n"
    "subject_b.py\n"
    "<<<<<<< SEARCH\n"
    "flag = 'OLD_B'\n"
    "=======\n"
    "flag = 'NEW_B'\n"
    ">>>>>>> REPLACE\n"
)


def _dispatch(req):
    offered = {t["function"]["name"] for t in (req.tools or [])}
    if req.tools is None:
        # BLOCK EDITOR phase: the whole response IS the edits.
        return _resp(text="Here are the *SEARCH/REPLACE* blocks:\n\n" + _BLOCKS)
    if "Edit" in offered:
        # Tool-loop EDITOR (reverted default): the F-B failure — never emits an Edit call.
        return _resp(text="I have reviewed the plan but will not make changes yet.")
    # ARCHITECT phase: emit a done envelope carrying a substantive plan.
    plan = ("1. In subject_a.py change flag 'OLD_A' -> 'NEW_A'.\n"
            "2. In subject_b.py change flag 'OLD_B' -> 'NEW_B'.")
    return _resp(text=json.dumps({"status": "done", "result": plan, "error_class": None,
                                  "error_number": None}))


def test_block_editor_produces_multifile_edits_the_toolloop_misses(tmp_path):
    a = tmp_path / "subject_a.py"
    b = tmp_path / "subject_b.py"
    a.write_text("flag = 'OLD_A'\n")
    b.write_text("flag = 'OLD_B'\n")

    domain = CodingDomain(name="coding")
    # Enable the block editor on this instance (a plain attribute the reverted code ignores,
    # so the RED run still imports and runs — it just falls through to the tool-loop editor).
    domain.block_editor_enabled = True

    dispatch = MagicMock(side_effect=_dispatch)
    with patch.object(InferenceDevice, "__init__", return_value=None), \
         patch.object(InferenceDevice, "dispatch", dispatch), \
         patch("unseen_university.system_alarms.raise_alarm"):
        domain.run(
            {"id": "T-block-editor-proof", "title": "flip both flags",
             "description": "OLD_A->NEW_A in subject_a.py and OLD_B->NEW_B in subject_b.py"},
            cwd=tmp_path,
        )

    ca, cb = a.read_text(), b.read_text()
    assert "NEW_A" in ca and "OLD_A" not in ca and "NEW_B" in cb and "OLD_B" not in cb, (
        "block editor must apply BOTH single-completion SEARCH/REPLACE blocks; files read "
        f"{ca!r} / {cb!r} — the tool-loop editor never chose to edit (F-B)"
    )
