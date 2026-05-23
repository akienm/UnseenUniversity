"""cc_inbox_bridge.py — Igor-side wrapper around lab.claudecode.cc_inbox.

Thin bridge so igor.cognition.* and igor.tools.* callers have a natural
import path: `from .cognition.cc_inbox_bridge import post_to_cc_inbox`.

The underlying cc_inbox.append() is already non-fatal on I/O error, but
this bridge adds a second safety net — any exception from the append call
is caught and logged (not raised), so a failing inbox never breaks a
triggering subsystem (consult escalate, ticket_trip, etc.).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("igor.cognition.cc_inbox_bridge")


def post_to_cc_inbox(
    kind: str,
    summary: str,
    body: str = "",
    ticket_id: Optional[str] = None,
    urgency: str = "normal",
    response_expected: bool = False,
) -> None:
    """Append an entry to CC's inbox. Fire-and-forget — never raises.

    kind: short category string for CC-side filtering
          (consult_escalate, ticket_trip, cloud_fallback_engaged, etc.)
    summary: one-line summary shown in /readinbox list
    body: longer detail shown on request
    ticket_id: optional — surfaces in inbox display, lets CC pivot to ticket
    urgency: low | normal | high
    response_expected: True if Igor is waiting for CC input (sets a flag CC
                       can use to prioritize which entries need a reply)
    """
    try:
        from lab.claudecode.cc_inbox import append as _append

        _append(
            kind=kind,
            summary=summary,
            body=body,
            ticket_id=ticket_id,
            urgency=urgency,  # type: ignore[arg-type]
            response_expected=response_expected,
        )
    except Exception as exc:
        log.debug("cc_inbox post failed (non-fatal): %s", exc)
