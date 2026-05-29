"""GrannyShim — lifecycle shim + MCP tool surface for GrannyWeatherwaxDevice."""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from typing import Optional

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _post_uc(path: str, body: dict, timeout: float = 5.0) -> Optional[dict]:
    """POST JSON to rack server. Returns response dict or None on failure."""
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{_UC_BASE}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = _SSL_CTX if _UC_BASE.startswith("https://") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning("GrannyShim: rack server call %s failed: %s", path, e)
        return None


class GrannyShim(BaseShim):
    _device_id = "granny-weatherwax"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        # Register CC worker dispatch_fn on the routing device
        try:
            from devices.granny.device import GrannyWeatherwaxDevice
            from devices.granny.dispatch import cc_dispatch_fn

            g = GrannyWeatherwaxDevice()
            g.register_worker(
                "cc",
                [
                    "Platform",
                    "Infrastructure",
                    "Cognition",
                    "Database",
                    "Training",
                    "Research",
                ],
                dispatch_fn=cc_dispatch_fn,
            )
            log.info("GrannyShim: CC worker registered with dispatch_fn")
        except Exception as e:
            log.warning("GrannyShim: CC worker registration failed: %s", e)

        result = _post_uc(
            "/api/agents/register",
            {
                "agent_id": "granny-weatherwax",
                "capabilities": [
                    "intake_ticket",
                    "route_ticket",
                    "escalate_to_cc",
                    "ticket_routing",
                ],
                "tmux_target": "granny",
            },
        )
        if result and result.get("status") == "ok":
            log.info("GrannyShim: registered with rack server")
        else:
            log.warning("GrannyShim: rack server unavailable — running unregistered")
        return True  # shim always starts; registration is best-effort

    def stop(self) -> bool:
        _post_uc("/api/agents/deregister", {"agent_id": "granny-weatherwax"})
        return True

    def restart(self) -> bool:
        self.stop()
        return self.start()

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
                        "**Completion criteria:** verify intake_ticket returns AuditResult shape"
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
