"""
Proof for the post-apply lint/test verdict gate (T-aider-port-verdict-gate).

The failure mode this kills: the editor self-reports done, nothing verifies the edit even PARSES —
"returns DONE, silently wrong." The discriminator (and proof node) is therefore NOT the happy
path but the anti-silent-wrongness case: a syntactically-broken applied edit must re-enter
reflection and, on exhaustion, carry a FAILURE rung — never a passing one. A test_green rung is
kept falsifiable by a red companion; test-couldn't-run is distinguished from test-red.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from unseen_university.devices.inference import verdict_gate
from unseen_university.agentic.architect_editor import ArchitectEditorFlow


# ── verdict_gate unit checks ──────────────────────────────────────────────────

def test_broken_edit_yields_compile_error_and_repair(tmp_path):
    (tmp_path / "m.py").write_text("def broken(\n")  # will not compile
    verdict, repair = verdict_gate.evaluate(tmp_path, ["m.py"], "no named test here")
    assert verdict.rung == verdict_gate.COMPILE_ERROR
    assert repair, "a compile failure must produce a repair message to re-enter reflection"


def test_clean_edit_without_test_caps_at_compile_ok(tmp_path):
    (tmp_path / "m.py").write_text("value = 1\n")
    verdict, repair = verdict_gate.evaluate(tmp_path, ["m.py"], "no test mentioned")
    assert verdict.rung == verdict_gate.COMPILE_OK and repair is None


def _with_test(tmp_path, value):
    (tmp_path / "svc.py").write_text(f"VALUE = {value}\n")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_svc.py").write_text(
        "import svc\n\n\ndef test_value():\n    assert svc.VALUE == 2\n"
    )


def test_named_green_test_yields_test_green(tmp_path):
    _with_test(tmp_path, 2)  # test expects 2 → passes
    verdict, repair = verdict_gate.evaluate(tmp_path, ["svc.py"], "verify with tests/test_svc.py")
    assert verdict.rung == verdict_gate.TEST_GREEN and repair is None


def test_named_red_test_yields_test_red_and_repair(tmp_path):
    """Red companion — without it, test_green is unfalsifiable (a hardcoded green would pass)."""
    _with_test(tmp_path, 3)  # test expects 2, code says 3 → fails
    verdict, repair = verdict_gate.evaluate(tmp_path, ["svc.py"], "verify with tests/test_svc.py")
    assert verdict.rung == verdict_gate.TEST_RED and repair


def test_unrunnable_test_is_unknown_not_green(tmp_path):
    """A test that CANNOT run (missing node) caps at the lint rung — never recorded as green."""
    (tmp_path / "svc.py").write_text("VALUE = 1\n")
    verdict, repair = verdict_gate.evaluate(tmp_path, ["svc.py"], "run tests/test_absent.py")
    assert verdict.rung == verdict_gate.COMPILE_OK, "an un-runnable test must not become test_green"
    assert repair is None, "an un-runnable test must not burn a reflection round"


# ── THE PROOF NODE — a broken edit never earns a passing verdict ──────────────

def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = r.output_tokens = 10
    r.cost_estimate = 0.0
    return r


class _FakeDev:
    def __init__(self, blocks, plan="Edit m.py."):
        self._blocks, self._plan, self.block_calls = blocks, plan, 0

    def dispatch(self, req):
        if req.tools is None:
            i = min(self.block_calls, len(self._blocks) - 1)
            self.block_calls += 1
            return _resp(self._blocks[i])
        return _resp(json.dumps({"status": "done", "result": self._plan,
                                 "error_class": None, "error_number": None}))


_BROKEN = "m.py\n<<<<<<< SEARCH\nvalue = 1\n=======\ndef broken(\n>>>>>>> REPLACE\n"


def test_broken_edit_final_verdict_is_failure_rung_not_passing(tmp_path):
    """A syntactically-broken applied edit re-enters reflection and ends on a FAILURE rung.

    This is the anti-silent-wrongness proof: without the gate the block editor returns DONE on
    'edits landed' with no idea the file no longer parses. With the gate, the DONE envelope's
    verdict is compile_error — a failure rung, NEVER a passing one, even after cap-exhaustion.
    """
    (tmp_path / "m.py").write_text("value = 1\n")
    dev = _FakeDev([_BROKEN])  # the model keeps producing a broken edit
    result = ArchitectEditorFlow(block_editor_enabled=True, inference_device=dev).run(
        system_prompt="", initial_message="edit m.py", ticket_id="T-verdict", cwd=tmp_path,
    )
    verdict = (result.envelope or {}).get("verdict", {})
    assert verdict.get("rung") == verdict_gate.COMPILE_ERROR, (
        f"a broken edit must carry a compile_error verdict, not a passing rung; got {verdict}"
    )
    assert verdict.get("rung") not in verdict_gate.PASSING_RUNGS
    assert dev.block_calls > 1, "a lint failure must re-enter the reflection loop"


def test_lint_failure_reflection_is_bounded_by_cap(tmp_path):
    """Lint failures consume the SAME bounded budget — total dispatches stay ≤ cap+1."""
    (tmp_path / "m.py").write_text("value = 1\n")
    dev = _FakeDev([_BROKEN])
    ArchitectEditorFlow(block_editor_enabled=True, inference_device=dev).run(
        system_prompt="", initial_message="edit m.py", ticket_id="T-cap", cwd=tmp_path,
    )
    assert dev.block_calls <= ArchitectEditorFlow.MAX_REFLECTIONS + 1
