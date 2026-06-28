"""
SummarizerDevice — URL/document → tiered output.

Accepts a URL or raw content string and produces three output tiers:
  exec   — 1-3 sentences (quick-scan)
  detail — 1 paragraph with key points (fuller summary)
  chunks — original text segmented into ~500-word blocks (for downstream processing)

All tiers computed when format='all'. Consumers select the depth they need:
  - Quick-scan (CC, Rack-Minion): exec tier
  - Nuanced reading (Igor): detail or chunks tier

HTTP API (when running standalone):
  POST /api/summarize
    body: {"url": str} | {"content": str} | both
    query: ?format=exec|detail|chunks|all  (default: all)
  → {"exec": str, "detail": str, "chunks": list[str]}

Inference backend: InferenceDevice (openrouter or ollama, configured via env).
URL fetching: urllib (stdlib only). No TheIgors imports. No Postgres.
"""

from __future__ import annotations

import html.parser
import logging
import os
import re
import socket
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Literal

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()
_PORT = int(os.environ.get("SUMMARIZER_PORT", "8085"))
_CHUNK_MAX_WORDS = int(os.environ.get("SUMMARIZER_CHUNK_WORDS", "500"))
_MODEL = os.environ.get("SUMMARIZER_MODEL", "openai/gpt-4o-mini")

FormatType = Literal["exec", "detail", "chunks", "all"]


# ── HTML stripping ─────────────────────────────────────────────────────────────


class _HTMLTextExtractor(html.parser.HTMLParser):
    _SKIP_TAGS = {"script", "style", "head", "nav", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html_bytes: bytes) -> str:
    try:
        text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = html_bytes.decode("latin-1", errors="replace")
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(text)
        return extractor.text()
    except Exception:
        # Fallback: crude tag strip
        return re.sub(r"<[^>]+>", " ", text)


# ── Content fetching ───────────────────────────────────────────────────────────


def _fetch_url(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "agent-datacenter-summarizer/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "html" in content_type:
                return _strip_html(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL fetch failed: {exc}") from exc


# ── Text chunking ──────────────────────────────────────────────────────────────


def _chunk_text(text: str, max_words: int = _CHUNK_MAX_WORDS) -> list[str]:
    """Split text into chunks of up to max_words words on paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else [""]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for para in paragraphs:
        words = para.split()
        para_words = len(words)

        # Paragraph itself exceeds max_words — split it directly
        if para_words > max_words:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_words = 0
            for i in range(0, para_words, max_words):
                chunks.append(" ".join(words[i : i + max_words]))
            continue

        if current and current_words + para_words > max_words:
            chunks.append("\n\n".join(current))
            current = [para]
            current_words = para_words
        else:
            current.append(para)
            current_words += para_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text]


# ── LLM summarization ──────────────────────────────────────────────────────────


def _llm_summarize(text: str, tier: Literal["exec", "detail"], inference) -> str:
    """Call the inference device to produce exec or detail summary."""
    from unseen_university.devices.inference.shim import InferenceRequest

    if tier == "exec":
        instruction = (
            "Summarize the following content in 1-3 sentences. "
            "Be direct and factual. No preamble."
        )
        max_tokens = 128
    else:
        instruction = (
            "Summarize the following content in one paragraph (4-8 sentences). "
            "Include the key points, findings, or arguments. No preamble."
        )
        max_tokens = 512

    # Truncate very long content to avoid token limits
    words = text.split()
    if len(words) > 3000:
        text = " ".join(words[:3000]) + "\n\n[content truncated]"

    req = InferenceRequest(
        messages=[{"role": "user", "content": f"{instruction}\n\n{text}"}],
        model=_MODEL,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    resp = inference.dispatch(req)
    return resp.text.strip()


# ── Device ─────────────────────────────────────────────────────────────────────


class SummarizerDevice(BaseDevice):
    """
    Rack device for tiered document summarization.

    Usage:
        device = SummarizerDevice()
        result = device.summarize(url="https://...", format="all")
        result = device.summarize(content="...", format="exec")
    """

    DEVICE_ID = "summarizer"

    def __init__(self, inference=None) -> None:
        super().__init__()
        self._inference = inference  # InferenceDevice instance; injected or lazy-loaded
        self._blocked = False

    def _get_inference(self):
        if self._inference is not None:
            return self._inference
        from unseen_university.devices.inference.device import InferenceDevice

        self._inference = InferenceDevice()
        return self._inference

    def summarize(
        self,
        *,
        url: str | None = None,
        content: str | None = None,
        format: FormatType = "all",
    ) -> dict:
        """
        Produce tiered output from a URL or content string.

        Returns dict with keys present based on format:
          exec   → str (1-3 sentence summary)
          detail → str (paragraph summary)
          chunks → list[str] (original text segmented)
        """
        if not url and not content:
            raise ValueError("provide url or content")

        if url and not content:
            content = _fetch_url(url)

        result: dict = {}

        if format in ("chunks", "all"):
            result["chunks"] = _chunk_text(content)

        if format in ("exec", "all"):
            try:
                result["exec"] = _llm_summarize(content, "exec", self._get_inference())
            except Exception as exc:
                log.warning("summarizer exec tier failed: %s", exc)
                result["exec"] = ""

        if format in ("detail", "all"):
            try:
                result["detail"] = _llm_summarize(
                    content, "detail", self._get_inference()
                )
            except Exception as exc:
                log.warning("summarizer detail tier failed: %s", exc)
                result["detail"] = ""

        return result

    # ── BaseDevice contract ────────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "SummarizerDevice",
            "version": "1.0.0",
            "purpose": "URL/document → tiered output (exec/detail/chunks)",
        }

    def requirements(self) -> dict:
        return {"deps": ["starlette", "uvicorn"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["exec", "detail", "chunks"],
            "formats": ["exec", "detail", "chunks", "all"],
            "model": _MODEL,
            "port": _PORT,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_only",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        inference_ok = False
        try:
            inf = self._get_inference()
            inference_ok = inf.health().get("status") == "healthy"
        except Exception:
            pass
        status = "healthy" if inference_ok else "degraded"
        return {
            "status": status,
            "detail": (
                "inference reachable" if inference_ok else "inference unavailable"
            ),
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", socket.gethostname()),
            "pid": os.getpid(),
            "port": _PORT,
            "launch_command": "python -m unseen_university.devices.summarizer.device",
        }

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        self._blocked = True

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._blocked = False


# ── Starlette HTTP app ─────────────────────────────────────────────────────────


def _make_app(device: SummarizerDevice | None = None):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    if device is None:
        device = SummarizerDevice()

    async def _api_summarize(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        url = body.get("url") or None
        content = body.get("content") or None
        fmt = request.query_params.get("format", body.get("format", "all"))

        if fmt not in ("exec", "detail", "chunks", "all"):
            return JSONResponse({"error": f"unknown format {fmt!r}"}, status_code=400)

        if not url and not content:
            return JSONResponse({"error": "provide url or content"}, status_code=400)

        try:
            result = device.summarize(url=url, content=content, format=fmt)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except Exception as exc:
            log.exception("summarize error")
            return JSONResponse({"error": "internal error"}, status_code=500)

        return JSONResponse(result)

    async def _api_health(request: Request):
        return JSONResponse(device.health())

    return Starlette(
        routes=[
            Route("/api/summarize", _api_summarize, methods=["POST"]),
            Route("/api/health", _api_health, methods=["GET"]),
        ]
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = _make_app()
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
