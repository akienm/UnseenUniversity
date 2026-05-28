"""
ReaderDevice — unified URI reader with pluggable output modes.

Accepts any URI (https://, calibre://, file://, blob://) via the URI resolver
(devices/reader/uri.py), caches to the blob store, and routes to an output mode:

  format='summary' → {exec: str, detail: str, chunks: list[str]}
    Same contract as SummarizerDevice. Exec is 1-3 sentences; detail is a
    paragraph; chunks are the original text in ~500-word blocks.

  format='nodes'   → list[dict]   (T-reader-node-mode — not yet implemented)

LLM calls go through InferenceDevice (lazy-loaded, injectable for tests).
SummarizerDevice is NOT retired — gate on proven production use.

D-reader-device-unified-uri-2026-05-28
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Literal

from unseen_university.device import INTERFACE_VERSION, BaseDevice

from .chunker import chunk_text
from .uri import FetchResult, fetch_uri

log = logging.getLogger(__name__)

_START_TIME = time.time()
_MODEL = os.environ.get("READER_MODEL", "openai/gpt-4o-mini")

SummaryResult = dict[str, Any]  # {exec: str, detail: str, chunks: list[str]}


# ── LLM summarization ──────────────────────────────────────────────────────────


def _llm_summarize(text: str, tier: Literal["exec", "detail"], inference: Any) -> str:
    """Call inference device to produce exec (1-3 sentences) or detail (paragraph)."""
    from devices.inference.shim import InferenceRequest

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


def _summary_from_text(text: str, inference: Any) -> SummaryResult:
    """Produce exec/detail/chunks from plain text content."""
    chunks = chunk_text(text)
    combined = " ".join(chunks)  # summarize across all chunks

    exec_str = ""
    detail_str = ""
    try:
        exec_str = _llm_summarize(combined, "exec", inference)
    except Exception as exc:
        log.warning("reader exec tier failed: %s", exc)

    try:
        detail_str = _llm_summarize(combined, "detail", inference)
    except Exception as exc:
        log.warning("reader detail tier failed: %s", exc)

    return {"exec": exec_str, "detail": detail_str, "chunks": chunks}


# ── Device ─────────────────────────────────────────────────────────────────────


class ReaderDevice(BaseDevice):
    """
    Rack device for unified URI reading with pluggable output modes.

    read(uri, format='summary') → {exec, detail, chunks}
    read(uri, format='nodes')   → list[dict]  (T-reader-node-mode)
    """

    DEVICE_ID = "reader"

    def __init__(self, inference: Any = None) -> None:
        super().__init__(device_id=self.DEVICE_ID)
        self._inference = inference  # injectable for tests; lazy-loaded in prod

    def _get_inference(self) -> Any:
        if self._inference is None:
            from devices.inference.device import InferenceDevice

            self._inference = InferenceDevice()
        return self._inference

    # ── Public read API ────────────────────────────────────────────────────────

    def read(
        self,
        uri: str,
        format: str = "summary",  # noqa: A002
        *,
        force_refresh: bool = False,
    ) -> SummaryResult | list[dict]:
        """Fetch URI and return output in the requested format.

        Args:
            uri: Any supported URI scheme.
            format: 'summary' → {exec, detail, chunks}; 'nodes' → list[dict].
            force_refresh: Re-fetch even if blob is cached.

        Returns:
            dict for format='summary', list for format='nodes'.
        """
        if format == "nodes":
            raise NotImplementedError(
                "format='nodes' is implemented in T-reader-node-mode"
            )
        if format != "summary":
            raise ValueError(
                f"Unknown format {format!r}. Supported: 'summary', 'nodes'"
            )

        result = fetch_uri(uri, force_refresh=force_refresh)
        return self._apply_summary_mode(result)

    def _apply_summary_mode(self, result: FetchResult) -> SummaryResult:
        """Run summary output mode on a fetched FetchResult."""
        if not result.content:
            # Binary formats (epub, pdf): text extraction is for output modes
            # that know the format. Return empty for now; node mode handles this.
            log.info(
                "reader summary: empty content for %s (binary format %s)",
                result.uri,
                result.content_type,
            )
            return {"exec": "", "detail": "", "chunks": []}

        return _summary_from_text(result.content, self._get_inference())

    # ── BaseDevice lifecycle ───────────────────────────────────────────────────

    def start(self) -> bool:
        log.info("ReaderDevice starting (version=%s)", INTERFACE_VERSION)
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return self.start()

    def rollback(self) -> None:
        pass

    def self_test(self) -> dict:
        return {
            "passed": True,
            "details": f"ReaderDevice v{INTERFACE_VERSION} ready; inference lazy-loaded",
            "uptime_seconds": time.time() - _START_TIME,
        }

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "ReaderDevice",
            "version": "1.0.0",
            "purpose": "URI fetch + blob cache + tiered output (summary/nodes)",
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "formats": ["summary", "nodes"],
            "model": _MODEL,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        return {"status": "healthy", "detail": "inference lazy-loaded"}

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
            "launch_command": "python -m devices.reader.device",
        }

    def block(self, reason: str) -> None:
        self._blocked = True

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._blocked = False
