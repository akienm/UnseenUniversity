"""
test_system_health.py — Tests for GET /api/system_health endpoint (#232).

No network calls. Patches cluster_router.router with a pre-built ClusterRouter
whose machines are set directly, bypassing _probe_machine and _build_machines.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.cognition.cluster_router import MachineInfo, ClusterRouter


def _make_router(*machines: MachineInfo) -> ClusterRouter:
    """Build a ClusterRouter with pre-supplied machines (no env-var scan)."""
    import threading

    r = ClusterRouter.__new__(ClusterRouter)
    r._machines = {m.name: m for m in machines}
    r._override = ""
    r._lock = threading.Lock()
    r._last_refresh = float("inf")  # skip refresh
    r._built = True
    return r


def _healthy(name="local", **kw) -> MachineInfo:
    defaults = dict(
        name=name,
        ollama_host="http://localhost:11434",
        primary_model="llama3",
        reasoning_model="",
        is_local=True,
        hostname="akiendelllinux",
        network_type="wired",
        ram_gb=32,
        healthy=True,
        load_score=0.75,
        response_ms=55.0,
        active_models=1,
        last_checked=1e12,
    )
    defaults.update(kw)
    return MachineInfo(**defaults)


def _call_endpoint(router: ClusterRouter) -> dict:
    """
    Call _api_system_health synchronously by patching the router singleton
    and invoking the handler directly.
    """
    import asyncio
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route

    # Import here so patches apply at import time
    import igor.web.server as srv

    with patch("igor.cognition.cluster_router.router", router):
        # force_refresh is a no-op: our router has _last_refresh=inf
        tc = TestClient(
            Starlette(routes=[Route("/api/system_health", srv._api_system_health)]),
            raise_server_exceptions=True,
        )
        resp = tc.get("/api/system_health")
    return resp.status_code, resp.json()


class TestSystemHealthEndpoint(unittest.TestCase):

    def test_single_healthy_machine(self):
        router = _make_router(_healthy())
        status, body = _call_endpoint(router)
        self.assertEqual(status, 200)
        self.assertIn("machines", body)
        self.assertIn("ts", body)
        self.assertIsNone(body["override"])
        machines = body["machines"]
        self.assertEqual(len(machines), 1)
        m = machines[0]
        self.assertEqual(m["name"], "local")
        self.assertTrue(m["healthy"])
        self.assertEqual(m["load_score"], 0.75)
        self.assertEqual(m["response_ms"], 55)
        self.assertEqual(m["active_models"], 1)
        self.assertEqual(m["primary_model"], "llama3")
        self.assertEqual(m["network_type"], "wired")
        self.assertEqual(m["ram_gb"], 32)

    def test_unhealthy_machine_reflected(self):
        router = _make_router(_healthy(healthy=False, load_score=0.0, response_ms=0.0))
        _, body = _call_endpoint(router)
        self.assertFalse(body["machines"][0]["healthy"])
        self.assertEqual(body["machines"][0]["load_score"], 0.0)

    def test_multiple_machines(self):
        local = _healthy("local", is_local=True)
        remote = _healthy(
            "yoga9i",
            ollama_host="http://192.168.1.10:11434",
            is_local=False,
            network_type="wired",
            ram_gb=64,
            load_score=0.9,
            reasoning_model="deepseek-r1:7b",
        )
        router = _make_router(local, remote)
        _, body = _call_endpoint(router)
        names = {m["name"] for m in body["machines"]}
        self.assertIn("local", names)
        self.assertIn("yoga9i", names)

    def test_override_reflected(self):
        router = _make_router(_healthy())
        router._override = "yoga9i"
        _, body = _call_endpoint(router)
        self.assertEqual(body["override"], "yoga9i")

    def test_no_machines_returns_empty_list(self):
        router = _make_router()
        status, body = _call_endpoint(router)
        self.assertEqual(status, 200)
        self.assertEqual(body["machines"], [])

    def test_load_score_rounded(self):
        router = _make_router(_healthy(load_score=0.12345678))
        _, body = _call_endpoint(router)
        # Should be rounded to 3dp
        self.assertEqual(body["machines"][0]["load_score"], 0.123)


if __name__ == "__main__":
    unittest.main()
