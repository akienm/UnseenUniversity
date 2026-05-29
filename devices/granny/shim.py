"""GrannyShim — lifecycle shim + MCP tool surface for GrannyWeatherwaxDevice."""

from __future__ import annotations

from unseen_university.shim import BaseShim


class GrannyShim(BaseShim):
    _device_id = "granny-weatherwax"

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
            from devices.granny.device import GrannyWeatherwaxDevice

            g = GrannyWeatherwaxDevice()
            result = g.intake_ticket(
                {
                    "id": "T-self-test",
                    "title": "self-test ticket",
                    "size": "S",
                    "description": (
                        "**Affected files:** none\n"
                        "**Scope boundary:** self-test only\n"
                        "**Test plan:** verify intake_ticket returns AuditResult shape"
                    ),
                }
            )
            if not hasattr(result, "passed"):
                return {"passed": False, "details": f"unexpected shape: {result}"}
            h = g.health()
            if h.get("status") not in ("healthy", "degraded"):
                return {"passed": False, "details": f"unexpected health shape: {h}"}
            return {
                "passed": True,
                "details": f"intake_ticket OK; health={h['status']}",
            }
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        pass

    # ── MCP tool surface ───────────────────────────────────────────────────────

    def intake_ticket(self, ticket: dict) -> dict:
        """Audit gate — validate ticket at filing time.

        Returns {"passed": bool, "reasons": list[str], "escalate_to_cc": bool}.
        """
        from devices.granny.device import GrannyWeatherwaxDevice

        g = GrannyWeatherwaxDevice()
        result = g.intake_ticket(ticket)
        return {
            "passed": result.passed,
            "reasons": result.reasons,
            "escalate_to_cc": result.escalate_to_cc,
        }

    def route_ticket(self, ticket: dict) -> dict:
        """Route a ticket to the best-weighted worker.

        Returns {"dispatched": bool, "worker_id": str}.
        """
        from devices.granny.device import GrannyWeatherwaxDevice

        g = GrannyWeatherwaxDevice()
        dispatched, worker_id = g.route_ticket(ticket)
        return {"dispatched": dispatched, "worker_id": worker_id}

    def edge_weights(self, tag: str) -> list[dict]:
        """Return routing edges for a tag, sorted by weight descending.

        Returns [{"worker_id": str, "weight": float}, ...].
        """
        from devices.granny.device import GrannyWeatherwaxDevice

        g = GrannyWeatherwaxDevice()
        return [{"worker_id": wid, "weight": w} for wid, w in g.get_edge_weights(tag)]

    def health(self) -> dict:
        """Return device health status."""
        from devices.granny.device import GrannyWeatherwaxDevice

        g = GrannyWeatherwaxDevice()
        return g.health()
