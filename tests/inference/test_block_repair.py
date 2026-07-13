"""
Proof for the reflection repair loop + rich repair errors (T-aider-port-reflection-repair-loop).

The hypothesis: a failed apply is no longer a dead attempt. A first-attempt mismatch produces a
repair message GROUNDED IN THE ACTUAL FILE (the real 'did you mean' lines, not a generic string),
a corrected second attempt applies, the reflection cap is enforced, and each (failure-class →
successful-repair) pair is logged to the corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from unseen_university.agentic.architect_editor import ArchitectEditorFlow
from unseen_university.agentic.block_apply import (
    BlockApplyResult,
    build_repair_message,
)


# ── THE PROOF NODE — repair is file-grounded, not generic ─────────────────────

def test_repair_message_quotes_actual_file_lines(tmp_path):
    """A failed block's repair message CONTAINS the real lines from the file (did-you-mean).

    The value of the reflection loop is a targeted, file-grounded repair (F-C/F-E) — a generic
    'no match' string carries zero repair information. Here the SEARCH is close-but-wrong
    (`req` vs `request`); the repair must surface the ACTUAL file line so the model can fix it.
    """
    f = tmp_path / "svc.py"
    # find_similar_lines matches whole lines as units (aider-faithful): the SEARCH shares its
    # first + last lines with the file but got the middle line wrong. The file's REAL middle line
    # appears in NEITHER the SEARCH nor the REPLACE, so it can only reach the message via
    # did-you-mean — a generic 'no match' string could never surface it.
    f.write_text('def handler(request):\n    log.info("processing now")\n    return process(request)\n')
    result = BlockApplyResult(
        applied=[],
        failed=[("svc.py",
                 'def handler(request):\n    log.info("start")\n    return process(request)\n',
                 'def handler(request):\n    log.info("start")\n    validate()\n    return process(request)\n')],
    )
    msg = build_repair_message(result, tmp_path)
    assert "processing now" in msg, (
        "repair must quote the ACTUAL file line (did-you-mean), not just echo the failed SEARCH; "
        f"message was:\n{msg}"
    )
    assert "did you mean" in msg.lower()


# ── Reflection loop e2e (via the flow with a fake inference device) ───────────

def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = r.output_tokens = 10
    r.cost_estimate = 0.0
    return r


_PLAN = json.dumps({"status": "done", "result": "Edit svc.py: bump value.",
                    "error_class": None, "error_number": None})
_BAD = "svc.py\n<<<<<<< SEARCH\nvalue = 999\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
_GOOD = "svc.py\n<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"


class _FakeDevice:
    """Architect (tools offered) → plan; block editor (tools None) → the next scripted block."""

    def __init__(self, block_scripts):
        self._scripts = block_scripts
        self.block_calls = 0

    def dispatch(self, req):
        if req.tools is None:
            i = min(self.block_calls, len(self._scripts) - 1)
            self.block_calls += 1
            return _resp(self._scripts[i])
        return _resp(_PLAN)


def _run(tmp_path, block_scripts):
    (tmp_path / "svc.py").write_text("value = 1\n")
    device = _FakeDevice(block_scripts)
    flow = ArchitectEditorFlow(block_editor_enabled=True, inference_device=device)
    result = flow.run(
        system_prompt="", initial_message="Bump value in svc.py to 2.",
        ticket_id="T-repair", cwd=tmp_path,
    )
    return device, result


def test_reflection_repairs_on_second_attempt(tmp_path):
    """A first-attempt mismatch, then a corrected block, applies — the loop repairs (not dies)."""
    device, result = _run(tmp_path, [_BAD, _GOOD])
    assert (tmp_path / "svc.py").read_text() == "value = 2\n", "the corrected block must apply"
    assert result.outcome == "done"
    assert device.block_calls == 2, f"one initial + one repair dispatch expected, got {device.block_calls}"


def test_reflection_cap_is_enforced(tmp_path):
    """An always-mismatching model stops after the bounded block loop + one whole-file fallback.

    Block reflection is capped at MAX_REFLECTIONS+1 dispatches; when block applies nothing, the
    P8 edit-format ladder adds exactly ONE whole-file fallback dispatch — still bounded, then
    escalates (the fallback also can't apply the mismatching content)."""
    device, result = _run(tmp_path, [_BAD])  # every attempt is bad, in both dialects
    assert device.block_calls == ArchitectEditorFlow.MAX_REFLECTIONS + 2, (
        f"bound = block-cap ({ArchitectEditorFlow.MAX_REFLECTIONS + 1}) + 1 whole-file fallback, "
        f"got {device.block_calls}"
    )
    assert result.outcome == "escalate" and (tmp_path / "svc.py").read_text() == "value = 1\n"


def test_repair_pair_logged_to_corpus(tmp_path, monkeypatch):
    """A successful repair writes a (failure-class → repaired) pair to the io_corpus."""
    corpus_dir = tmp_path / "corpus"
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(corpus_dir))
    _run(tmp_path, [_BAD, _GOOD])

    records = []
    for jf in corpus_dir.rglob("*.jsonl"):
        for line in jf.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    repair_pairs = [r for r in records if r.get("kind") == "editor_repair_pair"]
    assert repair_pairs, f"a repair pair must be logged; corpus records: {records}"
    assert repair_pairs[0]["outcome"] == "repaired" and repair_pairs[0]["role"] == "editor"
