"""
AkienShim — Akien as an addressable rack entity.

Akien is not a daemon. This shim:
  - Gives Akien's traffic a comms:// address (comms://akien/)
  - Points at ~/.unseen_university/akien/{inbox,outbox,ideas}
  - Returns identity + address info via who_am_i()

No running process is started or stopped. start()/stop() are no-ops.
The comms://akien/ channel is the entry point; the web UI routes
messages to/from it.
"""

from __future__ import annotations

import os
from pathlib import Path

from unseen_university.shim import BaseShim

_DATA_ROOT = (
    Path(os.environ.get("ADC_RUNTIME_ROOT", Path.home() / ".unseen_university"))
    / "akien"
)

DEVICE_ID = "akien"
ADDRESS = "comms://akien/"


class AkienShim(BaseShim):
    """Shim for Akien — the human on the rack."""

    @property
    def device_id(self) -> str:
        return DEVICE_ID

    def start(self) -> bool:
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
        for sub in ("inbox", "outbox", "ideas"):
            (_DATA_ROOT / sub).mkdir(exist_ok=True)
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return self.start()

    def rollback(self) -> None:
        pass

    def self_test(self) -> dict:
        ok = _DATA_ROOT.exists()
        return {
            "passed": ok,
            "details": f"data root {'exists' if ok else 'missing'}: {_DATA_ROOT}",
        }

    def who_am_i(self) -> dict:
        """Return identity and address information for this device."""
        return {
            "id": DEVICE_ID,
            "address": ADDRESS,
            "entity_type": "human",
            "data_home": str(_DATA_ROOT),
            "channels": {
                "inbox": "comms://akien/inbox",
                "outbox": "comms://akien/outbox",
                "ideas": "comms://akien/ideas",
            },
            "online": False,
        }


def who_am_i() -> dict:
    """Module-level access to identity information."""
    return AkienShim().who_am_i()
