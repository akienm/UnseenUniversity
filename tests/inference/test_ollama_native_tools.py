"""
Hermetic unit tests for T-ollama-local-native-tools — the local-Ollama native tool-calling
fix that unblocked the DS keystone smoke. No Hex required (mocked transport).

Three gaps the local Ollama path had (the OpenAI-format path already handled them):
  1. OllamaSource.call must forward req.tools into the /api/chat payload.
  2. _parse_response must extract message.tool_calls from an Ollama-format response.
  3. (arg-format handling lives inline in ToolLoop.run; covered by the live smoke.)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.device import _parse_response
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import OllamaSource


def test_parse_response_extracts_ollama_tool_calls():
    """The Ollama /api/chat branch must surface message.tool_calls (was content-only)."""
    tc = [{"function": {"name": "write", "arguments": {"file_path": "x", "content": "y"}}}]
    raw = {"message": {"content": "", "tool_calls": tc}, "done": True, "model": "devstral"}
    resp = _parse_response(raw)
    assert resp.tool_calls == tc


def test_parse_response_no_tool_calls_is_none():
    raw = {"message": {"content": "just prose"}, "done": True}
    assert _parse_response(raw).tool_calls is None


def test_ollama_source_forwards_tools_to_payload():
    """OllamaSource.call must include req.tools in the /api/chat payload."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({"message": {"content": "ok"}, "done": True}).encode()
        return resp

    src = OllamaSource(base_url="http://hex:11434")
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="devstral-small-2:24b",
        tools=[{"type": "function", "function": {"name": "write"}}],
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        src.call(req)
    assert "tools" in captured["body"], "req.tools must be forwarded to Ollama /api/chat"
    assert captured["body"]["tools"] == req.tools


def test_ollama_source_omits_tools_when_none():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({"message": {"content": "ok"}, "done": True}).encode()
        return resp

    src = OllamaSource(base_url="http://hex:11434")
    req = InferenceRequest(messages=[{"role": "user", "content": "hi"}], model="m", tools=None)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        src.call(req)
    assert "tools" not in captured["body"]
