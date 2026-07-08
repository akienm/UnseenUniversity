"""AiderProxyServer translates Ollama <-> InferenceDevice.dispatch and routes by TIER,
never a model (T-aider-through-inference-proxy). Uses a fake dispatch so no live source
is needed — the point is the translation + tier routing, not the backend."""

import json
import urllib.request

import pytest

from unseen_university.devices.inference.aider_proxy import AiderProxyServer, TIER_ALIAS
from unseen_university.devices.inference.shim import InferenceResponse


class _Recorder:
    def __init__(self):
        self.last = None

    def dispatch(self, req):
        self.last = req
        return InferenceResponse(text="hello from the tier", model="qwen3-coder:30b",
                                 input_tokens=11, output_tokens=3, cost_estimate=0.0,
                                 source_kind="local")


@pytest.fixture
def proxy():
    rec = _Recorder()
    srv = AiderProxyServer(rec.dispatch, port=0)  # port 0 = OS-assigned free port
    # ThreadingHTTPServer binds on start(); grab the real port after.
    srv.start()
    srv._port = srv._httpd.server_address[1]
    yield srv, rec
    srv.stop()


def _post(url, obj):
    r = urllib.request.urlopen(urllib.request.Request(
        url, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}))
    return json.loads(r.read())


def test_chat_routes_by_builder_tier_not_model(proxy):
    srv, rec = proxy
    out = _post(f"{srv.base_url}/api/chat", {
        "model": "uu-builder", "stream": False,
        "messages": [{"role": "user", "content": "fix the bug"}],
        "options": {"temperature": 0.2, "num_predict": 512},
    })
    # dispatch got a TIER request, not a pinned model
    assert rec.last.model == "", "must not pin the model aider named"
    assert rec.last.task_class == "worker" and rec.last.domain == "coding"
    assert rec.last.temperature == 0.2 and rec.last.max_tokens == 512
    # response is Ollama /api/chat shape carrying the dispatch result
    assert out["message"]["content"] == "hello from the tier"
    assert out["done"] is True
    assert out["eval_count"] == 3 and out["prompt_eval_count"] == 11


def test_stream_returns_ndjson_with_done(proxy):
    srv, _rec = proxy
    r = urllib.request.urlopen(urllib.request.Request(
        f"{srv.base_url}/api/chat",
        data=json.dumps({"model": "uu-builder", "stream": True,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"Content-Type": "application/json"}))
    lines = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
    assert lines[-1]["done"] is True
    assert "hello from the tier" in "".join(l["message"]["content"] for l in lines)


def test_tags_advertises_the_tier_alias(proxy):
    srv, _rec = proxy
    r = urllib.request.urlopen(f"{srv.base_url}/api/tags")
    names = [m["name"] for m in json.loads(r.read())["models"]]
    assert any(TIER_ALIAS in n for n in names)
