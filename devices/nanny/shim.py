"""NannyShim — lifecycle shim for NannyOggDevice."""

from __future__ import annotations

from unseen_university.shim import BaseShim


class NannyShim(BaseShim):
    _device_id = "nanny-ogg"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        try:
            from devices.nanny.device import NannyOggDevice

            d = NannyOggDevice()
            entries = d.list_entries()
            return {
                "passed": True,
                "details": f"{len(entries)} schedule entries loaded",
            }
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        pass

    def _handle_non_skill(self, text: str) -> str:
        try:
            from devices.nanny.device import NannyOggDevice

            d = NannyOggDevice()
            entries = d.list_entries()
            enabled = [e.entry_id for e in entries if e.enabled]
            return (
                f"Nanny Ogg here, dear. I've got {len(enabled)} things on the go: "
                + ", ".join(enabled[:3])
                + ("..." if len(enabled) > 3 else ".")
                + " What can I help with? Try /help."
            )
        except Exception:
            return "Nanny Ogg here. Try /help to see what I can do."
