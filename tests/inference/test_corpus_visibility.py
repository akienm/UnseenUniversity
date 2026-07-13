"""
corpus visibility — the starve-curve's numerator (warm hits) and its segmentation (role/turn).

Two gaps this closes (T-corpus-visibility-gaps):
  (a) A WARM HIT — a $0 pattern-cache intercept — used to return from dispatch BEFORE _capture_io
      fired, so the success event of the compiled-inference thesis wrote NO corpus record. The
      curve's numerator was invisible to the instrument meant to measure it.
  (b) Records carried no layer label, so architect vs editor was recoverable only by grepping
      system-prompt text.

THE PROOF NODE is ``test_warm_hit_writes_corpus_record_labeled_warm``: a dispatch forced down the
intercept path must write a corpus record with outcome='warm'. GREEN: the record is on disk.
RED (warm path returns before capture, as it did before this ticket): the corpus is empty and the
``warm`` assertion fails with an authentic AssertionError — the test reads a FILE and imports only
stable symbols, so the reverted parent still collects (no import/collection collateral).

The other two tests pin the role/turn labels: that _capture_io records them, and that the coding
loop threads role='architect' + the turn index into the request it dispatches.
"""
from __future__ import annotations

import json

import pytest

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import default_registry
from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse
from unseen_university.devices.inference.sources import SourceRegistry


@pytest.fixture
def device():
    dev = InferenceDevice(mode="openrouter", sources=SourceRegistry(), models=default_registry())
    yield dev
    dev._health.stop()


def _read_corpus(corpus_dir) -> list[dict]:
    recs = []
    for f in sorted(corpus_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def test_warm_hit_writes_corpus_record_labeled_warm(device, tmp_path, monkeypatch):
    """THE proof node: a $0 intercept hit produces a corpus record tagged outcome='warm'."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path))

    # Force the intercept path. dispatch does a function-local
    # `from ...pattern_intercept import try_intercept`, so patch the SOURCE module, not a
    # device-module name — anything else won't bind and the real try_intercept runs.
    served = InferenceResponse(
        text="WARM_SERVED_ANSWER", model="archivist-pattern-cache",
        source_kind="none", raw={"source": "pattern_intercept"},
    )
    monkeypatch.setattr(
        "unseen_university.devices.inference.pattern_intercept.try_intercept",
        lambda req, db_url=None: served,
    )

    resp = device.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "a question long enough to match a compiled habit"}],
        task_class="worker", ticket_id="T-warm-proof",
    ))
    assert resp.text == "WARM_SERVED_ANSWER"  # the warm answer was still served, not swallowed

    records = _read_corpus(tmp_path)
    warm = [r for r in records if r.get("outcome") == "warm"]
    assert warm, (
        "a warm ($0 intercept) hit must persist a corpus record tagged outcome='warm' — the "
        "starve-curve's numerator — but none was written; the warm path returned before capture"
    )
    rec = warm[-1]
    assert rec["ticket_id"] == "T-warm-proof"
    assert rec["dollars"] == 0.0
    assert "WARM_SERVED_ANSWER" in json.dumps(rec["response"])  # the served bytes are captured


def test_capture_io_records_role_and_turn(device, tmp_path, monkeypatch):
    """_capture_io stamps the coding-loop layer labels so the curve can be segmented."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path))
    device._capture_io(
        InferenceRequest(
            messages=[{"role": "user", "content": "x"}],
            role="architect", turn=3, parent_id="p-1",
        ),
        outcome="ok",
    )
    rec = _read_corpus(tmp_path)[-1]
    assert rec["role"] == "architect" and rec["turn"] == 3 and rec["parent_id"] == "p-1"


def test_agentic_loop_threads_role_into_request():
    """The coding loop labels its dispatch: role='architect' + the turn index ride the request."""
    from unseen_university.agentic.loop import AgenticLoop, TextToolCodec

    seen = []

    class _RecordingDevice:
        def dispatch(self, req):
            seen.append(req)
            # A terminal reply so the loop stops after one turn.
            return InferenceResponse(text="DONE: nothing to do", model="stub", finish_reason="stop")

    loop = AgenticLoop(codec=TextToolCodec(), inference_device=_RecordingDevice(), max_turns=1)
    loop.run(system_prompt="sys", initial_message="do it", role="architect")

    assert seen, "the loop must have dispatched at least one request"
    assert seen[0].role == "architect"
    assert seen[0].turn == 0
