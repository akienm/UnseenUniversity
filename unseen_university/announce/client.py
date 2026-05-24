"""
DatacenterClient — agent-side counterpart to AnnounceListener.

An agent process instantiates a DatacenterClient with its IdentityEnvelope
and a connection to the bus, calls announce(), and the client posts the
envelope to comms://announce + polls comms://announce-events for the
matching Manifest. Once cached, accessor methods expose tool bindings,
state refs, channel subscriptions, and surface addresses without the
caller having to re-parse the wire format.

Slice 3 scope: synchronous announce() with bounded poll. Slice 3b will
add IDLE-driven wakeup so re-announce on invalidation is push-based.

Generic across agent types — Igor, CC, research-orca all consume the
same client. Per-agent specialization composes around it (e.g. IgorShim
in slice 3b).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bus.envelope import Envelope
from bus.imap_server import IMAPServer

from .envelope import ANNOUNCE_MAILBOX, IdentityEnvelope
from .manifest import (
    ACL,
    ANNOUNCE_EVENTS_MAILBOX,
    INVALIDATE_MAILBOX,
    ChannelSubscription,
    StateRef,
    ToolBinding,
)

REGISTRY_TARGET = "registry"  # invalidates targeted at all clients

log = logging.getLogger(__name__)

DEFAULT_ANNOUNCE_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 0.05


class AnnounceTimeoutError(Exception):
    """Raised when announce() does not receive a Manifest within the timeout."""


class AnnounceRejectedError(Exception):
    """Raised when the broker publishes a kind=error reply for our envelope."""

    def __init__(self, error_kind: str, detail: str):
        super().__init__(f"announce rejected ({error_kind}): {detail}")
        self.error_kind = error_kind
        self.detail = detail


class DatacenterClient:
    """
    Agent-side announce + manifest cache.

    Args:
        identity:    IdentityEnvelope describing this process.
        imap_server: bus.IMAPServer used for posting + polling.
        from_device: identifier this client uses on outbound envelopes.
                     Defaults to identity.primary_mailbox.
    """

    def __init__(
        self,
        identity: IdentityEnvelope,
        imap_server: IMAPServer,
        from_device: str | None = None,
    ) -> None:
        self._identity = identity
        self._imap = imap_server
        self._from_device = from_device or identity.primary_mailbox
        self._manifest: dict | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def announce(
        self,
        timeout: float = DEFAULT_ANNOUNCE_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> dict:
        """
        Post our IdentityEnvelope to comms://announce and poll
        comms://announce-events until our Manifest arrives or timeout fires.

        Returns the manifest dict. Caches it for subsequent accessor calls.
        Raises AnnounceTimeoutError when no reply lands in timeout seconds.
        Raises AnnounceRejectedError when the broker returns kind=error.
        """
        envelope = Envelope.now(
            from_device=self._from_device,
            to_device=ANNOUNCE_MAILBOX,
            payload=self._identity.to_dict(),
        )
        self._imap.append(ANNOUNCE_MAILBOX, envelope)

        deadline = time.monotonic() + timeout
        target_mailbox = self._identity.primary_mailbox
        while time.monotonic() < deadline:
            for reply in self._imap.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX):
                if reply.to_device != target_mailbox:
                    # Not addressed to us — drop. (Stub mailbox is shared.)
                    continue
                kind = reply.payload.get("kind")
                if kind == "manifest":
                    self._manifest = reply.payload["manifest"]
                    return self._manifest
                if kind == "error":
                    raise AnnounceRejectedError(
                        error_kind=reply.payload.get("error_kind", "unknown"),
                        detail=reply.payload.get("detail", ""),
                    )
            time.sleep(poll_interval)

        raise AnnounceTimeoutError(
            f"No manifest received on {ANNOUNCE_EVENTS_MAILBOX} within "
            f"{timeout}s for {target_mailbox!r}"
        )

    def check_for_invalidate(
        self,
        reannounce_timeout: float = DEFAULT_ANNOUNCE_TIMEOUT,
        reannounce_poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> int:
        """
        Drain comms://invalidate, react to any envelope whose target matches
        this client's agent_id (or 'registry' which all clients heed) by
        re-announcing and replacing the cached manifest atomically. Returns
        the number of invalidates that triggered a re-announce.

        Non-matching invalidates (other agents) are dropped silently.
        Re-announce timeouts are caught — the cached manifest is left
        unchanged and the count for that envelope is not incremented.
        """
        try:
            envelopes = self._imap.fetch_unseen(INVALIDATE_MAILBOX)
        except Exception as exc:
            log.warning("client: fetch from %s failed: %s", INVALIDATE_MAILBOX, exc)
            return 0

        my_agent_id = self._identity.agent_id
        handled = 0
        relevant = False
        for env in envelopes:
            if env.payload.get("kind") != "invalidate":
                continue
            target = env.payload.get("target", "")
            if target == my_agent_id or target == REGISTRY_TARGET:
                relevant = True

        if not relevant:
            return 0

        # Coalesce: one re-announce satisfies all matched invalidates in this batch.
        try:
            self.announce(
                timeout=reannounce_timeout, poll_interval=reannounce_poll_interval
            )
            handled = 1
        except (AnnounceTimeoutError, AnnounceRejectedError) as exc:
            log.warning(
                "client: re-announce after invalidate failed: %s — keeping stale manifest",
                exc,
            )
        return handled

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def manifest(self) -> dict | None:
        """The cached manifest dict, or None if announce() hasn't run."""
        return self._manifest

    def get_tool(self, name: str) -> ToolBinding | None:
        for entry in self._manifest_field("tools"):
            if entry.get("name") == name:
                return ToolBinding(**entry)
        return None

    def get_tools(self) -> list[ToolBinding]:
        return [ToolBinding(**entry) for entry in self._manifest_field("tools")]

    def get_state_ref(self, name: str) -> StateRef | None:
        for entry in self._manifest_field("state_refs"):
            if entry.get("name") == name:
                return StateRef(**entry)
        return None

    def get_state_refs(self) -> list[StateRef]:
        return [StateRef(**entry) for entry in self._manifest_field("state_refs")]

    def get_channels(self) -> list[ChannelSubscription]:
        return [
            ChannelSubscription(**entry)
            for entry in self._manifest_field("subscriptions")
        ]

    def get_acl(self) -> ACL | None:
        if self._manifest is None:
            return None
        return ACL(**self._manifest.get("acl", {}))

    def get_surface_address(self, surface: str) -> str | None:
        if self._manifest is None:
            return None
        return self._manifest.get("surface_addresses", {}).get(surface)

    def get_primary_address(self) -> str | None:
        if self._manifest is None:
            return None
        return self._manifest.get("primary_address")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _manifest_field(self, key: str) -> list:
        if self._manifest is None:
            return []
        return self._manifest.get(key, []) or []
