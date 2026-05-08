"""
test_system_health.py — Tests for GET /api/system_health endpoint (#232, #342).

Mocks machine_manager.get_ranked_machines and cluster_router health cache.
No network calls.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

# Import MachineRecord for building test fixtures
from lab.utility_closet.machine_manager import MachineRecord


def _machine(
    hostname="local",
    ip="127.0.0.1",
    display_name=None,
    inference_rank=1,
    ollama_model="llama3",
    ollama_model_batch=None,
    network_type="wired",
    ram_gb=32,
    status="online",
    roles=None,
) -> MachineRecord:
    return MachineRecord(
        hostname=hostname,
        display_name=display_name or hostname,
        ip=ip,
        os="linux",
        cpu="i7",
        ram_gb=ram_gb,
        network_type=network_type,
        status=status,
        ollama_port=11434,
        ollama_model=ollama_model,
        ollama_model_batch=ollama_model_batch,
        inference_rank=inference_rank,
        in_use_hours=[],
        in_use_until=None,
        roles=roles or [],
        aliases=[],
        ssh_enabled=False,
        ssh_user=None,
        notes=None,
    )


def _call_endpoint(machines, healthy_map=None, in_use_map=None, override=""):
    """
    Call _api_system_health synchronously with mocked machine_manager + health cache.
    healthy_map: {ollama_host: bool} — None = not yet probed
    in_use_map: {hostname: bool}
    """
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    import igor.web.server as srv
    import threading

    if healthy_map is None:
        healthy_map = {}
    if in_use_map is None:
        in_use_map = {m.hostname: False for m in machines}

    # Build fake health cache: {host: (healthy, timestamp)}
    fake_cache = {host: (healthy, 1e12) for host, healthy in healthy_map.items()}
    fake_lock = threading.Lock()

    with patch(
        "lab.utility_closet.machine_manager.get_ranked_machines", return_value=machines
    ):
        with patch(
            "lab.utility_closet.machine_manager.is_in_use",
            side_effect=lambda h: in_use_map.get(h, False),
        ):
            with patch("igor.cognition.cluster_router._health_cache", fake_cache):
                with patch("igor.cognition.cluster_router._health_lock", fake_lock):
                    with patch.dict(
                        "os.environ", {"IGOR_INFERENCE_OVERRIDE": override}
                    ):
                        tc = TestClient(
                            Starlette(
                                routes=[
                                    Route("/api/system_health", srv._api_system_health)
                                ]
                            ),
                            raise_server_exceptions=True,
                        )
                        resp = tc.get("/api/system_health")
    return resp.status_code, resp.json()


class TestSystemHealthEndpoint(unittest.TestCase):

    def test_single_healthy_machine(self):
        m = _machine()
        status, body = _call_endpoint(
            [m],
            healthy_map={"http://127.0.0.1:11434": True},
        )
        self.assertEqual(status, 200)
        self.assertIn("machines", body)
        self.assertIn("ts", body)
        self.assertIsNone(body["override"])
        machines = body["machines"]
        self.assertEqual(len(machines), 1)
        entry = machines[0]
        self.assertEqual(entry["hostname"], "local")
        self.assertTrue(entry["healthy"])
        self.assertFalse(entry["in_use"])
        self.assertEqual(entry["network_type"], "wired")
        self.assertEqual(entry["ram_gb"], 32)
        self.assertEqual(entry["model"], "llama3")

    def test_unhealthy_machine_reflected(self):
        m = _machine()
        _, body = _call_endpoint(
            [m],
            healthy_map={"http://127.0.0.1:11434": False},
        )
        self.assertFalse(body["machines"][0]["healthy"])

    def test_not_yet_probed_healthy_is_none(self):
        m = _machine()
        _, body = _call_endpoint([m], healthy_map={})
        self.assertIsNone(body["machines"][0]["healthy"])

    def test_multiple_machines(self):
        local = _machine("local", ip="127.0.0.1", inference_rank=1)
        remote = _machine(
            "yoga9i", ip="10.0.0.90", inference_rank=2, network_type="wifi", ram_gb=16
        )
        _, body = _call_endpoint(
            [local, remote],
            healthy_map={
                "http://127.0.0.1:11434": True,
                "http://10.0.0.90:11434": True,
            },
        )
        names = {m["hostname"] for m in body["machines"]}
        self.assertIn("local", names)
        self.assertIn("yoga9i", names)

    def test_override_reflected(self):
        m = _machine()
        _, body = _call_endpoint([m], override="yoga9i")
        self.assertEqual(body["override"], "yoga9i")

    def test_no_override_returns_null(self):
        m = _machine()
        _, body = _call_endpoint([m], override="")
        self.assertIsNone(body["override"])

    def test_in_use_reflected(self):
        m = _machine(hostname="akiendell")
        _, body = _call_endpoint(
            [m],
            in_use_map={"akiendell": True},
        )
        self.assertTrue(body["machines"][0]["in_use"])

    def test_no_machines_returns_empty_list(self):
        status, body = _call_endpoint([])
        self.assertEqual(status, 200)
        self.assertEqual(body["machines"], [])


if __name__ == "__main__":
    unittest.main()
