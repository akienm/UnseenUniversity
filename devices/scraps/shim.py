"""ScrapsShim — lifecycle shim for ScrapsDevice (in-process, no subprocess)."""

from __future__ import annotations

from unseen_university.shim import BaseShim


class ScrapsShim(BaseShim):
    _device_id = "scraps"

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
            from devices.scraps.scraps_device import ScrapsDevice

            d = ScrapsDevice()
            result = d.validate_ticket(
                {
                    "title": "self-test ticket",
                    "description": "**Test plan:** call validate_ticket and check shape.",
                },
                silent=True,
            )
            if "valid" not in result or "issues" not in result:
                return {"passed": False, "details": f"unexpected shape: {result}"}
            return {
                "passed": True,
                "details": "validate_ticket returned expected shape",
            }
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        pass

    def _handle_non_skill(self, text: str) -> str:
        import hashlib
        _BARKS = ["Woof!", "Grr!", "Bark!", "Yip!", "Ruff!"]
        idx = int(hashlib.md5(text.encode()).hexdigest(), 16) % len(_BARKS)
        return _BARKS[idx]
