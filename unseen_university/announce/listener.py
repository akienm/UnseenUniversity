"""
AnnounceListener — pulls envelopes from comms://announce, dispatches to the
broker, publishes Manifest replies to comms://announce-events.

Slice 2: pump() is the testable seam — fetches unseen envelopes from the
announce mailbox and processes them synchronously. Production wraps this
in an IDLE loop (slice 3 adds the IDLE wakeup); tests call pump() directly.

Errors during dispatch publish a structured error envelope to
announce-events rather than letting the listener crash — losing one
envelope is far better than killing the broker.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict as _asdict

from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer

from .broker import AnnounceBroker, AnnounceError
from .channels import ChannelRegistry
from .envelope import ANNOUNCE_MAILBOX, IdentityEnvelope, ValidationError
from .manifest import ANNOUNCE_EVENTS_MAILBOX

log = logging.getLogger(__name__)


class AnnounceListener:
    """
    Wraps an AnnounceBroker with bus I/O.

    Args:
        broker:           AnnounceBroker instance (slice 1).
        imap_server:      bus handle (PgBus at runtime; legacy param name)
                          used for fetch + append + idle_wait.
        from_device:      identifier the listener uses on outbound envelopes.
                          Defaults to 'skeleton'.
        channel_registry: optional ChannelRegistry — when set, registers the
                          announcing agent's mailbox in each of its subscribed
                          channels after a successful manifest resolve.
    """

    def __init__(
        self,
        broker: AnnounceBroker,
        imap_server: IMAPServer,
        from_device: str = "skeleton",
        channel_registry: ChannelRegistry | None = None,
    ) -> None:
        self._broker = broker
        self._imap = imap_server
        self._from_device = from_device
        self._channels = channel_registry

    def pump(self) -> int:
        """
        Process all unseen envelopes in comms://announce. Returns the number
        of envelopes processed (success + error replies both counted).
        """
        try:
            envelopes = self._imap.fetch_unseen(ANNOUNCE_MAILBOX)
        except Exception as exc:
            log.warning("announce-listener: fetch failed: %s", exc)
            return 0

        for env in envelopes:
            self._handle_one(env)
        return len(envelopes)

    def _handle_one(self, env: Envelope) -> None:
        try:
            identity = IdentityEnvelope.from_dict(env.payload)
        except ValidationError as exc:
            self._publish_error(
                to_device=env.from_device or "unknown",
                error_kind="validation",
                detail=str(exc),
                original=env.payload,
            )
            return
        except Exception as exc:
            self._publish_error(
                to_device=env.from_device or "unknown",
                error_kind="parse",
                detail=str(exc),
                original=env.payload,
            )
            return

        try:
            manifest = self._broker.resolve_announce(identity)
        except AnnounceError as exc:
            self._publish_error(
                to_device=identity.primary_mailbox,
                error_kind="resolve",
                detail=str(exc),
                original=identity.to_dict(),
            )
            return
        except Exception as exc:
            log.exception("announce-listener: unexpected broker failure")
            self._publish_error(
                to_device=identity.primary_mailbox,
                error_kind="broker",
                detail=str(exc),
                original=identity.to_dict(),
            )
            return

        if self._channels is not None:
            for sub in manifest.subscriptions:
                self._channels.register_member(sub.name, identity.primary_mailbox)

        reply = Envelope.now(
            from_device=self._from_device,
            to_device=identity.primary_mailbox,
            payload={
                "kind": "manifest",
                "manifest": manifest.to_dict(),
            },
        )
        self._imap.append(ANNOUNCE_EVENTS_MAILBOX, reply)

    # ── IDLE-driven run loop (slice 3) ────────────────────────────────────────

    # RFC 2177: servers MAY drop IDLE after 29 min. We re-enter after 25 min
    # to stay inside the safe window.
    _IDLE_KEEPALIVE_S: float = 25 * 60

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Run the listener in an IDLE loop until stop is set.

        Wakeup sources:
        - Server pushes EXISTS (message arrived) → pump(), re-enter IDLE
        - 25-minute keepalive timeout → re-enter IDLE without pumping
        - stop.set() → exit cleanly

        stop=None runs until the process exits.
        """
        log.info("announce-listener: entering IDLE loop on %s", ANNOUNCE_MAILBOX)
        while stop is None or not stop.is_set():
            try:
                woke = self._imap.idle_wait(
                    ANNOUNCE_MAILBOX, timeout_s=self._IDLE_KEEPALIVE_S
                )
                if woke:
                    self.pump()
                # timeout → re-enter IDLE (keepalive cycle, no fetch needed)
            except Exception as exc:
                log.warning("announce-listener: IDLE loop error: %s", exc)
        log.info("announce-listener: IDLE loop stopped")

    def _publish_error(
        self,
        to_device: str,
        error_kind: str,
        detail: str,
        original: dict,
    ) -> None:
        log.warning(
            "announce-listener: %s error for %s — %s",
            error_kind,
            to_device,
            detail,
        )
        err = Envelope.now(
            from_device=self._from_device,
            to_device=to_device,
            payload={
                "kind": "error",
                "error_kind": error_kind,
                "detail": detail,
                "original": original,
            },
        )
        self._imap.append(ANNOUNCE_EVENTS_MAILBOX, err)
