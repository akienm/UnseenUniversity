"""
announce_mcp.py — CC-side MCP adapter for the announce protocol.

CC sessions are clients of the datacenter just like Igor. This module is
the thin wrapper Claude Code's MCP layer drives so a CC session can
announce itself, read its manifest, and react to invalidates without
knowing about the underlying IMAP bus.

Three tool methods, all returning JSON-friendly dicts:
  - announce()                — post identity envelope + cache the manifest
  - manifest()                — return the cached manifest (or {"manifest": None})
  - check_for_invalidate()    — drain comms://invalidate, re-announce on match

Errors are returned as {"error": "..."} rather than raised — matches MCP
convention where tool failures are data, not exceptions.

Slice 3b ships the adapter module. Wiring it into Claude Code's .mcp.json
is slice 5 work and lives outside the unseen_university repo.
"""

from __future__ import annotations

import logging
import os
import socket as _socket

from .client import (
    AnnounceRejectedError,
    AnnounceTimeoutError,
    DatacenterClient,
)
from .envelope import IdentityEnvelope

log = logging.getLogger(__name__)

DEFAULT_AGENT_ID = "cc"


class AnnounceMcpServer:
    """
    Singleton-style adapter: one DatacenterClient per process.

    CC sessions instantiate this once at MCP server startup. The first
    announce() call caches the manifest; subsequent manifest() calls are
    cheap dict lookups; check_for_invalidate() can be polled on a timer
    or in response to file-system signals.

    Args:
        instance_id: e.g. session id. Defaults to "{box}-{pid}".
        agent_id:    profile name to announce as (default "cc").
        box / box_n: address components; default to socket.gethostname() / 0.
        surfaces:    list of active surfaces in identity envelope.
    """

    def __init__(
        self,
        instance_id: str | None = None,
        agent_id: str = DEFAULT_AGENT_ID,
        box: str | None = None,
        box_n: int = 0,
        surfaces: list[str] | None = None,
    ) -> None:
        actual_box = box or _socket.gethostname()
        actual_instance = instance_id or f"{actual_box}-{os.getpid()}"
        self._client = DatacenterClient(
            agent_id=agent_id,
            instance=actual_instance,
            box=actual_box,
            box_n=box_n,
            pid=os.getpid(),
            surfaces=surfaces or ["console", "mcp"],
        )

    # ── MCP-style tool methods ────────────────────────────────────────────────

    def announce_tool(self, timeout: float = 5.0) -> dict:
        """Post our IdentityEnvelope and return the manifest dict."""
        try:
            manifest = self._client.announce(timeout=timeout)
            return {"ok": True, "manifest": manifest}
        except AnnounceTimeoutError as exc:
            return {"ok": False, "error": f"announce timed out: {exc}"}
        except AnnounceRejectedError as exc:
            return {
                "ok": False,
                "error": f"announce rejected: {exc.detail}",
                "error_kind": exc.error_kind,
            }
        except Exception as exc:
            log.warning("announce_mcp: unexpected announce failure: %s", exc)
            return {"ok": False, "error": f"unexpected: {exc}"}

    def manifest_tool(self) -> dict:
        """Return the cached manifest (or {manifest: None} if not announced yet)."""
        return {"ok": True, "manifest": self._client.manifest}

    def check_for_invalidate_tool(self, reannounce_timeout: float = 5.0) -> dict:
        """Drain invalidates and re-announce on match. Returns count handled."""
        try:
            count = self._client.check_for_invalidate(
                reannounce_timeout=reannounce_timeout
            )
            return {"ok": True, "handled": count, "manifest": self._client.manifest}
        except Exception as exc:
            log.warning("announce_mcp: check_for_invalidate failed: %s", exc)
            return {"ok": False, "error": f"unexpected: {exc}"}

    # ── Inspection helpers ────────────────────────────────────────────────────

    @property
    def identity(self) -> IdentityEnvelope:
        return self._client.identity

    @property
    def client(self) -> DatacenterClient:
        return self._client
