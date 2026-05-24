"""
notify.py — notification intent predicate (T-swarm-notification-rule).

Rule: notify only when an envelope carries clear intent for the recipient.
  1. Direct address: envelope.to_device == recipient (case-insensitive).
  2. @-mention: "@recipient" appears anywhere in the payload text (case-insensitive).

An envelope broadcast to a shared channel with no @-mention is NOT intent —
observability channels are silent unless the recipient is specifically targeted.
"""

from __future__ import annotations


def has_intent(envelope: dict, recipient: str) -> bool:
    """
    Return True when envelope is specifically directed at recipient.

    Args:
        envelope:  Plain dict parsed from bus Envelope JSON (keys: from_device,
                   to_device, payload, ...).
        recipient: Mailbox name to check against, e.g. "CC.0".
    """
    to_device = envelope.get("to_device", "")
    if to_device.lower() == recipient.lower():
        return True

    mention = f"@{recipient}".lower()
    payload = envelope.get("payload", {})
    return _payload_has_mention(payload, mention)


def _payload_has_mention(obj, mention: str) -> bool:
    """Recursively scan obj for mention in any string value."""
    if isinstance(obj, str):
        return mention in obj.lower()
    if isinstance(obj, dict):
        return any(_payload_has_mention(v, mention) for v in obj.values())
    if isinstance(obj, list):
        return any(_payload_has_mention(item, mention) for item in obj)
    return False
