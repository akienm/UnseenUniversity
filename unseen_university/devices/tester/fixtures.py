"""Network fixtures — the failures we actually MET, served on demand.

Every fixture here reproduces a defect that was MEASURED on 2026-07-13/14, not one that was
imagined. That distinction is the point: a fixture invented from a spec tests the spec. A fixture
built from an incident tests reality, and reality is what shipped the bug.

Until now NONE of these was reproducible in a test. The extractor was ~97% broken for a month and
every individual record was well-formed; the ollama that "could not infer" was answering metadata
in 300µs; the reasoning model returned HTTP 200 with nothing in it. All three were invisible to
the suite because the suite could not make a model misbehave on purpose. Now it can.

The trick that makes it possible (T-tester-owns-the-network): inside the sandbox we are root in a
user namespace, so we claim `10.0.0.100` as our own address and BE Hex. The code under test does
not change. It dials the same endpoint it always dials.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler

# The six models Hex really has, so /api/tags looks exactly like the real thing.
_MODELS = [
    "qwen3-coder:30b", "deepseek-r1:32b", "qwen2.5-coder:14b",
    "deepseek-r1:14b", "devstral-small-2:24b", "llama3.2:3b",
]
_TAGS = {"models": [{"name": m, "model": m, "size": 1} for m in _MODELS]}


class _Base(BaseHTTPRequestHandler):
    """Speaks just enough ollama to be indistinguishable from it, until it isn't."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # never spam the test output
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            return self.on_tags()
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path.startswith("/api/chat") or self.path.startswith("/api/generate"):
            return self.on_chat()
        self._json(404, {"error": "not found"})

    # Subclasses override these two.
    def on_tags(self):
        self._json(200, _TAGS)

    def on_chat(self):
        self._json(200, {"message": {"role": "assistant", "content": '{"intent":"refactor","confidence":0.9}'},
                         "done": True, "done_reason": "stop"})


class Healthy(_Base):
    """A model that works. The control — without it, the others prove nothing."""


class Saturated(_Base):
    """THE BUG OF 2026-07-13, and the one that cost the most.

    `/api/tags` answers in 300µs — because it is served by the Go parent and never touches the
    inference slot. `/api/chat` never returns — because the single slot (`-np 1`) is hours deep.

    **METADATA FAST + WORK NEVER IS THE SIGNATURE OF A QUEUE, NOT A CORPSE**, and every health
    probe we own reports this source as HEALTHY, because they all probe REACHABILITY —
    exactly the property that stays true while capacity is zero.

    This is the discriminating fixture T-healthmonitor-probes-reachability-not-capability needs:
    a capability probe must call this DOWN; a reachability probe calls it fine.
    """

    HANG_SECONDS = 600.0

    def on_chat(self):
        time.sleep(self.HANG_SECONDS)   # the queue you joined and will never leave


class TruncatedReasoner(_Base):
    """`deepseek-r1:14b`, measured 2026-07-14: HTTP 200 carrying NOTHING.

    A reasoning model's whole token budget is eaten by its `<think>` trace, so it is cut off before
    emitting a single token of answer: `finish_reason='length'`, `content=''`. `json.loads("")` then
    raises `Expecting value: line 1 column 1 (char 0)` — and the extractor files it as a PARSE
    failure. It is not. It is a BUDGET failure, and the two have different fixes.

    Two causes, one signal. The record must state which.
    """

    def on_chat(self):
        self._json(200, {"message": {"role": "assistant", "content": ""},
                         "done": True, "done_reason": "length"})


class ArrayAnswer(_Base):
    """`llama3.2:3b`, measured 2026-07-14: handed ten few-shot examples, it classifies ALL ELEVEN.

    It reads the few-shot block as a BATCH and returns a JSON *array*. `parsed.get()` on a list then
    raises `AttributeError` three frames from the cause — the original
    crash-masquerading-as-a-refusal, whose `intent='unknown'` was byte-identical to CP1's honest
    "I don't know."

    A failure that impersonates a virtue recruits your own values to camouflage itself.
    """

    def on_chat(self):
        raw = json.dumps([
            {"intent": "Error handling", "confidence": 0.9},
            {"intent": "Test reliability", "confidence": 0.8},
            {"intent": "Escalation policy refinement", "confidence": 0.82},
        ])
        self._json(200, {"message": {"role": "assistant", "content": raw},
                         "done": True, "done_reason": "stop"})


class NoModels(_Base):
    """Reachable, cheerful, and empty. A 200 is not a capability."""

    def on_tags(self):
        self._json(200, {"models": []})

    def on_chat(self):
        self._json(404, {"error": "model not found"})


FIXTURES = {
    "healthy": Healthy,
    "saturated": Saturated,
    "truncated_reasoner": TruncatedReasoner,
    "array_answer": ArrayAnswer,
    "no_models": NoModels,
}
