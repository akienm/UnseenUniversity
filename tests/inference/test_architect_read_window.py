"""
Architect read-window + prose-plan-salvage proof (T-architect-read-window-unblock).

D-coding-loop-redesign-aider-survey-2026-07-04. The inference I/O corpus (2026-07-05) showed the
read-only ARCHITECT on devstral@Hex never reaches a plan for two harness reasons: (1) aci_mode's
windowed Read returns 100 lines/call, so the planner pages through files forever and burns its
turn budget (it also runs the full suite mid-plan); (2) even when it plans, it emits prose rather
than escaped JSON, so parse_terminal_envelope fails → LOOP_ESCALATE → the editor never runs and
the real plan is thrown away. Windowed Read was built for the EDITOR; it cripples the ARCHITECT.

THE PROOF NODE — `test_substantive_prose_plan_reaches_the_editor` — drives the PUBLIC surface
`ArchitectEditorFlow.run()` with AgenticLoop stubbed: the architect stub finishes NON-DONE
(LOOP_ESCALATE) carrying a substantive numbered plan as plain text (the prose-drift failure).
GREEN (salvage active at HEAD): the flow salvages the plan and runs the editor stub → the editor
ran. RED (salvage reverted): a non-DONE architect returns early → the editor stub is never
constructed → the "editor ran" assertion fails with an authentic AssertionError.

Revert-safety (proof_emitter_gotchas): this module imports ONLY stable symbols at top level
(ArchitectEditorFlow, LoopResult, LOOP_ESCALATE, LOOP_DONE) — never the impl-added
`_is_substantive_plan`/`_select_tool_defs`/`_FULL_READ_DEF`, which are imported LOCALLY inside the
coverage tests so the reverted tree still collects cleanly and reaches the assertion.
"""

from __future__ import annotations

from unittest.mock import patch

from unseen_university.devices.inference.agentic_loop import (
    LOOP_DONE,
    LOOP_ESCALATE,
    LoopResult,
)
from unseen_university.devices.inference.architect_editor import ArchitectEditorFlow


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


class _StubLoop:
    """Stub for AgenticLoop: the architect returns a prose-plan escalate; the editor records.

    Distinguishes the two roles by `tool_names` (the architect is constructed with Read/Bash;
    the editor with tool_names=None) — a discriminator present in BOTH the pre- and
    post-implementation trees, so the revert produces an authentic red rather than a vacuous one
    (plan_mode is NOT passed by the reverted flow, so it cannot tell the roles apart).
    """

    editor_ran: bool = False

    def __init__(self, **kwargs):
        self._is_architect = kwargs.get("tool_names") is not None

    def run(self, **kwargs):
        if self._is_architect:
            # ARCHITECT: a REAL numbered plan, but finished as prose/escalate (no clean DONE
            # envelope) — exactly the corpus-observed prose-drift the salvage must rescue.
            return LoopResult(
                LOOP_ESCALATE,
                text=(
                    "1. Edit /repo/subject.py: replace `value = 1` with `value = 2`\n"
                    "2. Edit /repo/other.py: add the import at the top\n"
                ),
                turns=4,
            )
        # EDITOR: record that it was reached, then terminate cleanly.
        _StubLoop.editor_ran = True
        return LoopResult(LOOP_DONE, text="applied", turns=1)


def test_substantive_prose_plan_reaches_the_editor():
    """A substantive plan produced without a clean DONE envelope still runs the editor.

    GREEN (salvage): architect escalates WITH a substantive numbered plan → the flow salvages it
    → the editor stub runs. RED (salvage reverted): the non-DONE architect returns early → the
    editor is never constructed → `editor_ran` stays False → AssertionError. The plan is prose,
    not escaped JSON, so a build that depends on json.loads succeeding cannot pass this.
    """
    _StubLoop.editor_ran = False
    with patch("unseen_university.devices.inference.architect_editor.AgenticLoop", _StubLoop):
        result = ArchitectEditorFlow(aci_mode=True).run(
            system_prompt="sys",
            initial_message="do the thing",
            ticket_id="T-proof",
        )
    assert _StubLoop.editor_ran, (
        "a substantive prose/escalate plan must be salvaged and handed to the editor; the flow "
        "threw the real plan away because it was not a clean DONE envelope — the exact "
        f"prose-drift failure the corpus observed. Flow returned outcome={result.outcome!r}"
    )


# ── COVERAGE (local imports — not run by proof_emitter's single node) ─────────


def test_full_read_returns_the_whole_file(tmp_path):
    """plan_mode Read returns every line of a >100-line file (windowed mode returns only 100)."""
    from unseen_university.devices.inference.agentic_loop import _tool_read

    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line{i}" for i in range(250)))
    out = _tool_read(str(f), tmp_path, aci_mode=True, full_read=True)
    assert "line0" in out and "line249" in out, "full_read must include the whole file"
    assert "call Read offset=" not in out, "full_read must NOT be a windowed pager"


def test_windowed_read_still_pages_without_full_read(tmp_path):
    """Regression guard: the editor's windowed read is unchanged (full_read defaults off)."""
    from unseen_university.devices.inference.agentic_loop import _tool_read

    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line{i}" for i in range(250)))
    out = _tool_read(str(f), tmp_path, aci_mode=True, offset=0)
    assert "line0" in out and "line249" not in out, "windowed read must still page"
    assert "call Read offset=100" in out, "windowed read must still offer the scroll hint"


def test_plan_mode_bash_deflects_the_full_suite():
    """plan_mode Bash deflects a broad pytest run without spawning a subprocess."""
    from pathlib import Path

    from unseen_university.devices.inference.agentic_loop import _tool_bash

    out = _tool_bash("cd repo && .venv/bin/python3 -m pytest tests/ -q --tb=short", Path("."),
                     plan_mode=True)
    assert "[planning]" in out and "editor will run the tests" in out
    # A targeted single-file run is still allowed through (contains a .py node).
    passthrough = _tool_bash("pytest tests/inference/test_x.py::test_y", Path("."), plan_mode=True)
    assert "[planning]" not in passthrough, "a targeted single-file run must not be deflected"


def test_select_tool_defs_swaps_read_in_plan_mode():
    """plan_mode offers the full-file Read def (no offset); the editor keeps the windowed one."""
    from unseen_university.devices.inference.agentic_loop import _select_tool_defs

    plan_defs = _select_tool_defs(aci_mode=True, plan_mode=True, tool_names=["Read", "Bash"])
    read = next(t for t in plan_defs if t["function"]["name"] == "Read")
    assert "offset" not in read["function"]["parameters"]["properties"], (
        "plan_mode Read must be the whole-file def (no offset/window)"
    )
    editor_defs = _select_tool_defs(aci_mode=True, plan_mode=False, tool_names=None)
    editor_read = next(t for t in editor_defs if t["function"]["name"] == "Read")
    assert "offset" in editor_read["function"]["parameters"]["properties"], (
        "the editor must keep the windowed Read (offset param present)"
    )


def test_is_substantive_plan_rejects_garbage():
    """The salvage guard accepts a real plan but rejects empty/refusal prose."""
    from unseen_university.devices.inference.architect_editor import _is_substantive_plan

    assert _is_substantive_plan("1. Edit /repo/foo.py: change A to B\n2. run tests")
    assert not _is_substantive_plan("I cannot do this.")
    assert not _is_substantive_plan("")
