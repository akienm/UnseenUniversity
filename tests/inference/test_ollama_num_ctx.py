"""
Hermetic unit tests for T-ollama-source-set-num-ctx.

The OllamaSource /api/chat payload never set options.num_ctx, so ollama fell back to a
small default context window and SILENTLY TRUNCATED the prompt — the DS.0 observe-run
(2026-07-03, devstral-small-2:24b@Hex) made 114 tool calls / 0 edits because the model
was working blind on a truncated tail. These tests pin that every ollama request carries
an explicit options.num_ctx (a sane default, and a per-source configurable knob).

No Hex required — the transport is mocked and we inspect the real request body.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import OllamaSource


def _capture_body(src: OllamaSource, req: InferenceRequest) -> dict:
    """Call src with a mocked transport and return the JSON body sent to /api/chat."""
    captured: dict = {}

    def fake_urlopen(http_req, timeout=None):
        captured["body"] = json.loads(http_req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({"message": {"content": "ok"}, "done": True}).encode()
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        src.call(req)
    return captured["body"]


def test_ollama_payload_sets_num_ctx_default():
    """An unconfigured OllamaSource must still send an explicit options.num_ctx default."""
    src = OllamaSource(base_url="http://hex:11434")
    req = InferenceRequest(messages=[{"role": "user", "content": "hi"}], model="devstral-small-2:24b")
    body = _capture_body(src, req)
    assert "num_ctx" in body["options"], "ollama request must carry options.num_ctx (else silent truncation)"
    assert body["options"]["num_ctx"] == OllamaSource.DEFAULT_NUM_CTX
    assert body["options"]["num_ctx"] >= 32768, "default context window must be large enough to hold a real ticket"


def test_ollama_num_ctx_is_configurable_per_source():
    """The num_ctx knob is per-source so ops can tune it to the model + box RAM."""
    src = OllamaSource(base_url="http://hex:11434", num_ctx=65536)
    req = InferenceRequest(messages=[{"role": "user", "content": "hi"}], model="devstral-small-2:24b")
    body = _capture_body(src, req)
    assert body["options"]["num_ctx"] == 65536


def test_ollama_num_ctx_coexists_with_generation_options():
    """num_ctx must not clobber temperature / num_predict — all three ride in options."""
    src = OllamaSource(base_url="http://hex:11434")
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}], model="m", temperature=0.2, max_tokens=256
    )
    opts = _capture_body(src, req)["options"]
    assert opts["temperature"] == 0.2
    assert opts["num_predict"] == 256
    assert opts["num_ctx"] == OllamaSource.DEFAULT_NUM_CTX
