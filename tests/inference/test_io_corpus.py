"""
Inference I/O corpus — EVERY byte between upstream and downstream is captured (for training).

Hard rule (Akien, 2026-07-04): the complete request handed to the model (system + messages +
tools) and the complete raw response handed back are persisted on every dispatch. Before this,
only metadata (cost_record) was logged — the bytes were lost.

THE PROOF NODE is test_dispatch_captures_every_byte: it drives a REAL InferenceDevice.dispatch
through a mock source and then reads the corpus file off disk. GREEN: the corpus holds a record
carrying BOTH the distinctive request-message bytes AND the distinctive raw-response bytes. RED
(capture not wired into dispatch): nothing is written, the corpus glob is empty, and the
`records` assertion fails with an authentic AssertionError (the test imports only stable symbols
and reads a FILE — never the new io_corpus module — so the reverted parent still collects).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, default_registry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import SourceRegistry

_REQ_TOKEN = "UPSTREAM_BYTES_a9f3c1"
_RESP_TOKEN = "DOWNSTREAM_BYTES_7b2e88"


@pytest.fixture
def device():
    dev = InferenceDevice(mode="openrouter", sources=SourceRegistry(), models=default_registry())
    yield dev
    dev._health.stop()


def _local_mock():
    src = MagicMock()
    src.name = "ollama"
    src.available = True
    src.is_local = True
    src.billing_type = "usage_based"  # the real Hex shape
    src.call.return_value = {
        "choices": [{"message": {"content": f"here is the plan: {_RESP_TOKEN}"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        "model": "local-test",
    }
    return src


def _read_corpus(corpus_dir) -> list[dict]:
    recs = []
    for f in sorted(corpus_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def test_dispatch_captures_every_byte(device, tmp_path, monkeypatch):
    """A real dispatch persists a corpus record carrying the full request AND raw response bytes."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path))

    device._sources.register(_local_mock())
    device._models.register(
        ModelSpec(model_id="local-test", tier="t2",
                  input_cost_per_1m=0.0, output_cost_per_1m=0.0, context_window=8192, tags=[])
    )
    # Reachability lives on the connections stack now — wire the synthetic edge explicitly
    # so the pinned dispatch can reach it (mirrors the device's connections+policies=[] build).
    conns = ConnectionsRegistry()
    conns.register(Connection("local-test", "ollama", 0.0))
    device._rules = RulesEngine(device._sources, device._models, connections=conns, policies=[])

    device.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": f"do the task {_REQ_TOKEN}"}],
        system="SYSTEM_PROMPT_BYTES",
        model="local-test", task_class="worker", pin_reason="inference_test",
        ticket_id="T-corpus-proof",
    ))

    records = _read_corpus(tmp_path)
    assert records, (
        "the inference boundary must persist an I/O record for every dispatch, but the corpus is "
        "empty — no bytes were captured (capture is not wired into dispatch)"
    )
    blob = json.dumps(records)
    assert _REQ_TOKEN in blob, "the UPSTREAM request-message bytes were not captured"
    assert _RESP_TOKEN in blob, "the DOWNSTREAM raw-response bytes were not captured"

    rec = records[-1]
    assert rec["ticket_id"] == "T-corpus-proof"
    assert rec["request"]["system"] == "SYSTEM_PROMPT_BYTES"
    assert rec["request"]["messages"][0]["content"].endswith(_REQ_TOKEN)
    assert _RESP_TOKEN in json.dumps(rec["response"]["raw"])  # the literal provider payload
    assert rec["outcome"] == "ok"


def test_no_source_dispatch_still_captures_the_request(device, tmp_path, monkeypatch):
    """Even a failed (no live source) dispatch captures the upstream request — outcome=error."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path))
    device.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": f"orphan {_REQ_TOKEN}"}], task_class="worker",
    ))
    records = _read_corpus(tmp_path)
    assert records and any(r["outcome"] == "error" for r in records)
    assert _REQ_TOKEN in json.dumps(records)


def test_corpus_capture_roundtrips(tmp_path, monkeypatch):
    """Unit: io_corpus.capture writes a schema-stamped, round-trippable JSONL record."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path))
    from unseen_university.devices.inference import io_corpus

    path = io_corpus.capture({"outcome": "ok", "request": {"messages": [{"x": _REQ_TOKEN}]}})
    assert path is not None
    recs = _read_corpus(tmp_path)
    assert len(recs) == 1
    assert recs[0]["schema"] == io_corpus.SCHEMA == "inference.io.v1"
    assert recs[0]["id"] and recs[0]["ts"]  # correlation stamped
    assert recs[0]["request"]["messages"][0]["x"] == _REQ_TOKEN
