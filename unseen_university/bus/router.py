"""
Router — comms:// URI resolver and message dispatcher.

The comms:// scheme is the only addressing layer in unseen_university. Callers are
topology-blind: they address by name; the router handles IMAP APPEND dispatch.

Pub/sub falls out naturally from IMAP IDLE: comms://Shared → all IDLE subscribers
receive the message when they next poll or wake from IDLE. No separate pub/sub
layer is needed.

URI shape:
    comms://{mailbox_name}[.{surface}[.{sub-surface}...]]

The mailbox_name is the registered IMAP folder. Optional dot-separated
suffix segments after a registered mailbox name carry surface qualifiers
(e.g. comms://myhost.1.console resolves to mailbox myhost.1
with surface='console'). The router peels suffixes longest-prefix-wins
until a registered mailbox matches.

Examples:
    comms://Shared                       → mailbox Shared
    comms://CC.0                         → mailbox CC.0
    comms://igor-wild-0001               → mailbox igor-wild-0001
    comms://igor-wild-0001.console       → mailbox igor-wild-0001 (surface=console)
    comms://myhost.1             → mailbox myhost.1
    comms://myhost.1.inference   → mailbox myhost.1 (surface=inference)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bus.envelope import Envelope

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer

log = logging.getLogger(__name__)

_SCHEME = "comms://"


class AddressError(Exception):
    """Raised when a comms:// address cannot be resolved to a known mailbox."""


class Router:
    """
    Resolves comms:// URIs to IMAP mailboxes and dispatches messages.

    send() = resolve() + imap_server.append(). The split exists so callers
    can validate addresses before composing messages.

    Self-healing: if the IMAP server raises ConnectionError on send(), Router
    delegates to BusLauncher.relaunch() and retries once. If relaunch fails or
    the bus is blocked, BusBlockedError / BusUnavailableError propagate to caller.
    Pass bus_launcher=None (default) to disable self-healing (e.g. in tests).
    """

    def __init__(
        self,
        imap_server: "IMAPServer",
        bus_launcher: "BusLauncher | None" = None,
    ) -> None:
        self._imap = imap_server
        self._launcher = bus_launcher

    def resolve(self, address: str) -> str:
        """
        Parse mailbox_name from a comms:// address and verify it exists.

        Returns the mailbox name (the bare name, not the full URI). When the
        full path is not a registered mailbox, the router peels trailing
        dot-segments and retries — so comms://igor-wild-0001.console resolves
        to mailbox 'igor-wild-0001' if the full string isn't itself a folder.

        Raises AddressError if the URI is malformed or no prefix is registered.
        """
        mailbox, _surface = self._resolve_with_surface(address)
        return mailbox

    def extract_surface(self, address: str) -> str | None:
        """
        Return the surface qualifier (text after the resolved mailbox name) or
        None when the full address is itself a registered mailbox. Used by the
        announce listener and other surface-aware consumers.
        """
        _mailbox, surface = self._resolve_with_surface(address)
        return surface

    def _resolve_with_surface(self, address: str) -> tuple[str, str | None]:
        if not address.startswith(_SCHEME):
            raise AddressError(
                f"Invalid address {address!r}: must start with 'comms://'"
            )
        path = address[len(_SCHEME) :]
        if not path:
            raise AddressError(f"Invalid address {address!r}: mailbox name is empty")

        known = self._imap.list_mailboxes()
        if path in known:
            return path, None

        segments = path.split(".")
        for cut in range(len(segments) - 1, 0, -1):
            candidate = ".".join(segments[:cut])
            if candidate in known:
                surface = ".".join(segments[cut:])
                return candidate, surface

        raise AddressError(
            f"Unknown mailbox for address {address!r} (no prefix registered). "
            f"Known mailboxes: {known}"
        )

    def send(self, address: str, envelope: Envelope) -> None:
        """
        Dispatch an envelope to the mailbox identified by the comms:// address.

        Resolves the address first — raises AddressError immediately if unknown.
        On ConnectionError, delegates to BusLauncher for self-healing if configured.
        """
        from unseen_university.bus.bus_launcher import BusBlockedError, BusLauncher

        if self._launcher is not None and self._launcher.is_blocked():
            raise BusBlockedError(
                "Bus is blocked — operator must manually clear block before sending."
            )

        try:
            mailbox = self.resolve(address)
            self._imap.append(mailbox, envelope)
        except (ConnectionError, OSError) as exc:
            if self._launcher is None:
                raise
            log.warning("Bus unreachable on send to %r: %s", address, exc)
            relaunched = self._launcher.relaunch()
            if not relaunched:
                from unseen_university.bus.bus_launcher import BusUnavailableError

                raise BusUnavailableError(
                    f"Bus relaunch failed — cannot deliver to {address!r}"
                ) from exc
            # Retry once after successful relaunch
            mailbox = self.resolve(address)
            self._imap.append(mailbox, envelope)
            log.info("Retry after relaunch succeeded for %r", address)
