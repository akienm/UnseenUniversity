"""
channels.py — multi-party channel registry and fan-out (T-swarm-channel-mechanism).

A channel is a named room. When an agent announces, its subscriptions are
registered here. send_to_channel() fans out a single envelope to every
member's personal inbox (mirrored-inbox model: one copy per recipient, not
one shared IMAP folder all readers poll).

The `shared` channel is always present from init. Other channels are
created on-demand when the first member joins.

Fan-out is best-effort: delivery failures for individual recipients are
logged and skipped — one broken mailbox should not block delivery to others.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bus.envelope import Envelope

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer

log = logging.getLogger(__name__)

_SHARED = "shared"


class ChannelRegistry:
    """
    In-memory membership registry for bus channels.

    Thread-safety: not required for v1 (single-threaded announce pump).
    """

    def __init__(self) -> None:
        self._members: dict[str, list[str]] = {_SHARED: []}

    def register_member(self, channel: str, mailbox: str) -> None:
        """Add mailbox to channel's member list (idempotent)."""
        if channel not in self._members:
            self._members[channel] = []
        if mailbox not in self._members[channel]:
            self._members[channel].append(mailbox)
            log.debug(
                "channel %r: +%s (total %d)",
                channel,
                mailbox,
                len(self._members[channel]),
            )

    def unregister_member(self, channel: str, mailbox: str) -> None:
        """Remove mailbox from channel (no-op if not present)."""
        if channel in self._members and mailbox in self._members[channel]:
            self._members[channel].remove(mailbox)
            log.debug(
                "channel %r: -%s (total %d)",
                channel,
                mailbox,
                len(self._members[channel]),
            )

    def members(self, channel: str) -> list[str]:
        """Return a snapshot of member mailboxes for channel (empty list if unknown)."""
        return list(self._members.get(channel, []))

    def channels(self) -> list[str]:
        """Return all known channel names."""
        return list(self._members.keys())

    def fan_out(self, channel: str, envelope: Envelope, imap: "IMAPServer") -> int:
        """
        Deliver a copy of envelope to every member's personal inbox.

        Returns the number of successful deliveries. Failures are logged
        and skipped so one broken mailbox doesn't halt the others.
        """
        targets = self.members(channel)
        if not targets:
            log.debug("channel %r: fan_out with no members — no deliveries", channel)
            return 0

        delivered = 0
        for mailbox in targets:
            try:
                imap.append(mailbox, envelope)
                delivered += 1
            except Exception as exc:
                log.warning(
                    "channel %r: delivery to %r failed: %s — skipping",
                    channel,
                    mailbox,
                    exc,
                )
        log.debug("channel %r: delivered %d/%d", channel, delivered, len(targets))
        return delivered
