"""
Architect/editor split proof for the coding domain (T-coding-architect-editor-split).

D-coding-loop-redesign-aider-survey-2026-07-04. A single small model asked to
orient+plan+edit in one ReAct stream never reaches an edit (2026-07-04 DS.0
observe-runs: 0 Write/Edit ATTEMPTS across 149 tool calls). aider's architect
mode splits the jobs: a planner (no edit tools) resolves the task into a file-change
plan; a cheap editor turns that plan into an actual edit.

THE PROOF: drive the PUBLIC surface `CodingDomain().run(ticket)` with a mocked
InferenceDevice. The dispatch mock keys on whether the Edit tool is offered in the
request:
  - Edit NOT offered  → ARCHITECT phase → return a done-envelope carrying an edit PLAN
    (target file + old/new strings).
  - Edit offered + a plan present in the transcript → EDITOR phase → emit an Edit built
    FROM that plan (content pinned to the plan, not a free-floating mock).
  - Edit offered + NO plan in the transcript → the single-model path (no split): the
    model has an edit instruction from nobody, emits prose, never edits.

GREEN (split active at HEAD): architect plans → editor applies → the target file
contains the planned NEW text. RED (impl reverted → CodingDomain inherits the
single-loop walk): Edit is offered from turn 0 but no architect ran, so no plan is in
the transcript → the editor mock never edits → the file is untouched → the content
assertion fails with an authentic AssertionError.

Revert-safety: this module imports ONLY stable symbols (CodingDomain, InferenceDevice,
LoopResult) and names no impl-only symbol (architect_editor, _run_attempt,
architect_editor_enabled), so the reverted run imports cleanly and reaches the assertion
rather than dying at collection (proof_emitter_gotchas: red must be AssertionError, not
ImportError/NameError).
"""

from __future__ import annotations

import json
import re
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


_PLAN_RE = re.compile(
    r'\{[^{}]*"file_path"[^{}]*"old_string"[^{}]*"new_string"[^{}]*\}'
)


def _make_dispatch(target_path: str):
    """Return a dispatch side-effect implementing the architect/editor role split.

    Keys on the request's offered tools + transcript, NOT on any call counter, so it
    behaves correctly for both the two-phase split and the single-loop fallback.
    """
    plan_json = json.dumps(
        {"file_path": target_path, "old_string": "OLD_MARKER", "new_string": "NEW_MARKER"}
    )

    def dispatch(req):
        offered = {t["function"]["name"] for t in (req.tools or [])}
        edit_offered = "Edit" in offered
        blob = " ".join(
            m.get("content") or "" for m in req.messages if isinstance(m.get("content"), str)
        )

        if not edit_offered:
            # ARCHITECT phase (no edit tools): emit a done envelope carrying the plan.
            return _resp(text=json.dumps({"status": "done", "result": "planned", "plan": plan_json}))

        # Edit IS offered → editor phase OR single-loop fallback.
        if "OK: edited" in blob:
            # The edit already applied this run → terminate cleanly.
            return _resp(text=json.dumps({"status": "done", "result": "applied"}))

        m = _PLAN_RE.search(blob)
        if m:
            # EDITOR phase: build the Edit FROM the plan the architect handed down.
            plan = json.loads(m.group(0))
            return _resp(tool_calls=[{
                "id": "call_edit_1", "type": "function",
                "function": {"name": "Edit", "arguments": json.dumps(plan)},
            }])
        # Single-loop fallback: an edit instruction from nobody → the model can't edit.
        return _resp(text="No plan was provided; I cannot determine the change to make.")

    return dispatch


def _run(tmp_path):
    target = tmp_path / "subject.py"
    target.write_text("value = 'OLD_MARKER'\n")
    dispatch = MagicMock(side_effect=_make_dispatch(str(target)))
    with patch.object(InferenceDevice, "__init__", return_value=None), \
         patch.object(InferenceDevice, "dispatch", dispatch), \
         patch("unseen_university.system_alarms.raise_alarm"):
        result = CodingDomain(name="coding").run(
            {"id": "T-split-proof", "title": "swap the marker", "description": "OLD_MARKER -> NEW_MARKER"}
        )
    return target, result


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_architect_plan_flows_to_editor_and_edit_is_applied(tmp_path):
    """The split makes the edit happen; the single-loop path leaves the file untouched.

    GREEN: architect plans (Edit not offered) → editor applies the plan → the file's
    content becomes NEW_MARKER. RED (split reverted): Edit is offered from turn 0 with no
    architect-produced plan in the transcript → the editor never edits → the file still
    reads OLD_MARKER → AssertionError. The edit CONTENT is pinned to the plan (NEW_MARKER
    originates in the architect's plan, not the editor mock), so a hollow mock-invocation
    can't pass it.
    """
    target, _ = _run(tmp_path)
    contents = target.read_text()
    assert "NEW_MARKER" in contents and "OLD_MARKER" not in contents, (
        "architect/editor split must apply the planned edit (OLD_MARKER→NEW_MARKER); "
        f"file still reads: {contents!r} — the single-model path never reaches an edit"
    )
