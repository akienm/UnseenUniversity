"""
T-inference-ollama-honest-liveness: OllamaSource liveness must mean DISPATCH-ability, not a
bare socket connect. A hung/mis-served ollama holds the socket open while its HTTP API
404s/times out — a socket ping would report that dead server as live and the selector would
route a call that then dies. ping() now probes GET /api/tags: a 200 with a non-empty model
list is available; a 404, a hang, or an empty list is not. Fail-soft — the probe never raises
into the health loop.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.sources import OllamaSource

_URLOPEN = "unseen_university.devices.inference.sources.urllib.request.urlopen"


def _tags_response(models):
    raw = json.dumps({"models": models}).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_tags_200_with_models_is_available():
    src = OllamaSource()
    with patch(_URLOPEN, return_value=_tags_response([{"name": "qwen2.5-coder:14b"}])):
        assert src.ping() is True
        assert src.check_and_update() is True
        assert src.available is True


def test_chat_path_404_is_unavailable():
    """A 404 (wrong endpoint / server not serving the API) → unavailable, not a false 'live'."""
    src = OllamaSource()
    err = urllib.error.HTTPError(url="http://127.0.0.1:11434/api/tags", code=404,
                                 msg="Not Found", hdrs=None, fp=None)
    with patch(_URLOPEN, side_effect=err):
        assert src.ping() is False
        assert src.check_and_update() is False
        assert src.available is False


def test_empty_model_list_is_unavailable():
    """Server up but nothing pulled → cannot dispatch → unavailable (honest)."""
    src = OllamaSource()
    with patch(_URLOPEN, return_value=_tags_response([])):
        assert src.ping() is False


def test_hang_or_refused_is_unavailable():
    """A hung/refused server (URLError, incl. socket timeout) → unavailable."""
    src = OllamaSource()
    with patch(_URLOPEN, side_effect=urllib.error.URLError("connection refused")):
        assert src.ping() is False


def test_probe_failure_never_raises_into_health_loop():
    """Fail-soft: any probe exception is swallowed to False, never propagated."""
    src = OllamaSource()
    with patch(_URLOPEN, side_effect=RuntimeError("unexpected boom")):
        # must not raise
        assert src.ping() is False
        assert src.check_and_update() is False


def test_socket_open_but_api_dead_is_not_falsely_live():
    """The core bug: a bare socket ping reported a dead API as live. Now an API failure
    (even with the port open) reads as unavailable."""
    src = OllamaSource()
    # urlopen reaching the API and failing (500) is the 'socket up, API dead' case.
    err = urllib.error.HTTPError(url="http://127.0.0.1:11434/api/tags", code=500,
                                 msg="Internal Server Error", hdrs=None, fp=None)
    with patch(_URLOPEN, side_effect=err):
        assert src.ping() is False
