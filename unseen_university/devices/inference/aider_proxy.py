"""AiderProxyServer — a LIMITED Ollama-compatible HTTP door in front of the inference
proxy, so the external aider CLI routes through InferenceDevice.dispatch() instead of
hitting Hex/Ollama directly (T-aider-through-inference-proxy, D: Inference-Proxy-only +
tier-not-model).

Deliberately tiny (stdlib http.server, no web framework): it speaks just enough of the
Ollama dialect for aider/litellm — POST /api/chat, GET /api/tags, POST /api/show — and
hands every chat off to `dispatch_fn` (InferenceDevice.dispatch). All the real work —
tier→source selection, cloud escalation, budget-ledger cost, io_corpus capture — already
lives in dispatch(); this module adds ONLY the translation layer.

aider names a TIER-ALIAS model (`ollama_chat/uu-builder`), not a real model; the proxy
ignores the name and routes by {domain=coding, task_class=worker}. Point aider's
OLLAMA_API_BASE at this server.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

# The tier-alias aider names as its "model". Routing is by tier, not this string.
TIER_ALIAS = "uu-builder"
DEFAULT_PORT = 11434 + 66  # 11500 — distinct from Ollama's 11434 (which dispatch routes to)


def _to_inference_request(body: dict):
    """Ollama /api/chat body -> InferenceRequest, routing by the builder TIER (never a
    model). Imported lazily to avoid a shim<->aider_proxy import cycle."""
    from .shim import InferenceRequest

    opts = body.get("options") or {}
    return InferenceRequest(
        messages=body.get("messages") or [],
        model="",  # route by domain/tier — NEVER pin the model aider named
        domain="coding",
        task_class="worker",
        temperature=float(opts.get("temperature", 0.0)),
        max_tokens=int(opts.get("num_predict") or body.get("max_tokens") or 4096),
        foreground=True,  # aider is latency-sensitive builder work
        agent_id="Aider.0",
        tools=body.get("tools"),
    )


def _ollama_chat_response(resp) -> dict:
    """InferenceResponse -> Ollama /api/chat (non-stream) shape."""
    return {
        "model": resp.model or TIER_ALIAS,
        "created_at": "1970-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": resp.text or ""},
        "done": True,
        "done_reason": resp.finish_reason or "stop",
        "prompt_eval_count": resp.input_tokens,
        "eval_count": resp.output_tokens,
        # non-standard, harmless extras for our own observability
        "uu_source_kind": resp.source_kind,
        "uu_cost_estimate": resp.cost_estimate,
    }


def _make_handler(dispatch_fn):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence default stderr access log
            pass

        def _json(self, code: int, obj: dict, *, ndjson_lines=None):
            if ndjson_lines is not None:
                payload = ("".join(json.dumps(l) + "\n" for l in ndjson_lines)).encode()
            else:
                payload = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def do_GET(self):
            if self.path.rstrip("/") == "/api/tags":
                self._json(200, {"models": [{
                    "name": f"{TIER_ALIAS}:latest", "model": f"{TIER_ALIAS}:latest",
                    "modified_at": "1970-01-01T00:00:00Z", "size": 0, "digest": "",
                    "details": {"family": "uu-proxy", "parameter_size": "tier:worker"},
                }]})
            else:
                self._json(404, {"error": f"not found: {self.path}"})

        def do_POST(self):
            path = self.path.rstrip("/")
            body = self._read_body()
            if path == "/api/show":
                self._json(200, {"details": {"family": "uu-proxy"},
                                 "model_info": {}, "template": ""})
                return
            if path not in ("/api/chat", "/v1/chat/completions"):
                self._json(404, {"error": f"not found: {self.path}"})
                return
            try:
                req = _to_inference_request(body)
                resp = dispatch_fn(req)
            except Exception as exc:  # never leak a 500 without a body aider can read
                log.warning("AiderProxy: dispatch failed: %s", exc)
                self._json(500, {"error": f"inference dispatch failed: {exc}"})
                return
            out = _ollama_chat_response(resp)
            log.info("AiderProxy: routed builder-tier -> model=%s source=%s cost=%.4f in=%d out=%d",
                     out["model"], resp.source_kind, resp.cost_estimate,
                     resp.input_tokens, resp.output_tokens)
            if body.get("stream"):
                # Minimal NDJSON stream: one content chunk, then the done chunk.
                self._json(200, {}, ndjson_lines=[
                    {"model": out["model"], "created_at": out["created_at"],
                     "message": {"role": "assistant", "content": out["message"]["content"]},
                     "done": False},
                    {"model": out["model"], "created_at": out["created_at"],
                     "message": {"role": "assistant", "content": ""}, "done": True,
                     "done_reason": out["done_reason"],
                     "prompt_eval_count": out["prompt_eval_count"],
                     "eval_count": out["eval_count"]},
                ])
            else:
                self._json(200, out)

    return _Handler


class AiderProxyServer:
    """Threaded Ollama-compatible HTTP door forwarding to `dispatch_fn`. start()/stop()."""

    def __init__(self, dispatch_fn, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self._dispatch_fn = dispatch_fn
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = ThreadingHTTPServer((self._host, self._port), _make_handler(self._dispatch_fn))
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="aider-proxy", daemon=True)
        self._thread.start()
        log.info("AiderProxyServer: listening on %s (routes aider -> builder tier)", self.base_url)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            log.info("AiderProxyServer: stopped")
