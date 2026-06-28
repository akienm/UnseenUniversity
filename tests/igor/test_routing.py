"""
test_routing.py — Tests for #342 simplified router: machine_manager + cluster_router.

No network calls — DB and Ollama probing are mocked throughout.
"""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock


from unseen_university.devices.igor.tools.machine_manager import MachineRecord

# ── Helpers ───────────────────────────────────────────────────────────────────


def _machine(
    hostname="testhost",
    ip="10.0.0.1",
    inference_rank=1,
    in_use_hours=None,
    in_use_until=None,
    status="online",
    ollama_model="llama3.2:1b",
    ollama_model_batch=None,
    network_type="wired",
    ram_gb=32,
    roles=None,
    aliases=None,
) -> MachineRecord:
    return MachineRecord(
        hostname=hostname,
        display_name=hostname,
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
        in_use_hours=in_use_hours or [],
        in_use_until=in_use_until,
        roles=roles or [],
        aliases=aliases or [],
        ssh_enabled=False,
        ssh_user=None,
        notes=None,
    )


# ── MachineRecord ─────────────────────────────────────────────────────────────


class TestMachineRecord(unittest.TestCase):
    def test_ollama_host_with_ip(self):
        m = _machine(ip="10.0.0.99")
        self.assertEqual(m.ollama_host, "http://10.0.0.99:11434")

    def test_ollama_host_no_ip(self):
        m = _machine(ip=None)
        self.assertIn("localhost", m.ollama_host)

    def test_model_for_always_returns_single_local_model(self):
        # 2026-04-18: the two-column scheme (ollama_model / ollama_model_batch)
        # collapsed to a single local model per machine. model_for() now
        # ignores call_type and always returns ollama_model.
        m = _machine(ollama_model="qwen2.5:7b", ollama_model_batch="qwen2.5:14b")
        self.assertEqual(m.model_for("extraction"), "qwen2.5:7b")
        self.assertEqual(m.model_for("batch"), "qwen2.5:7b")
        self.assertEqual(m.model_for("tier2"), "qwen2.5:7b")
        self.assertEqual(m.model_for("preparse"), "qwen2.5:7b")


# ── is_in_use ─────────────────────────────────────────────────────────────────


class TestIsInUse(unittest.TestCase):
    """Tests for machine_manager.is_in_use — mocks get_machine."""

    def _run(self, m: MachineRecord) -> bool:
        # is_in_use lives in devices.igor.tools.machine_manager. Internal calls to
        # get_machine / _write_override resolve in the canonical module's
        # namespace — patch THERE, not on any re-export shim.
        from unseen_university.devices.igor.tools.machine_manager import is_in_use

        with patch("unseen_university.devices.igor.tools.machine_manager.get_machine", return_value=m):
            with patch("unseen_university.devices.igor.tools.machine_manager._write_override"):
                return is_in_use(m.hostname)

    def test_no_hours_no_override_available(self):
        m = _machine(in_use_hours=[], in_use_until=None)
        self.assertFalse(self._run(m))

    def test_indefinite_override_blocks(self):
        m = _machine(in_use_until="indefinite")
        self.assertTrue(self._run(m))

    def test_future_override_blocks(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        m = _machine(in_use_until=future)
        self.assertTrue(self._run(m))

    def test_expired_override_clears_and_returns_false(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        m = _machine(in_use_until=past)
        # Expired → should return False (and write None to clear)
        result = self._run(m)
        self.assertFalse(result)

    def test_in_use_hours_window_active(self):
        hour = datetime.now().hour
        # Window that includes current hour
        start = hour
        end = (hour + 1) % 24
        if start < end:
            m = _machine(in_use_hours=[[start, end]])
            self.assertTrue(self._run(m))

    def test_in_use_hours_window_not_active(self):
        hour = datetime.now().hour
        # Window 3 hours ahead — not current
        start = (hour + 3) % 24
        end = (hour + 4) % 24
        if start < end:
            m = _machine(in_use_hours=[[start, end]])
            self.assertFalse(self._run(m))

    def test_unknown_host_returns_false(self):
        from unseen_university.devices.igor.tools.machine_manager import is_in_use

        with patch("unseen_university.devices.igor.tools.machine_manager.get_machine", return_value=None):
            self.assertFalse(is_in_use("nonexistent"))


# ── resolve_alias ─────────────────────────────────────────────────────────────


class TestResolveAlias(unittest.TestCase):
    def test_hostname_match(self):
        from unseen_university.devices.igor.tools.machine_manager import resolve_alias

        m = _machine(hostname="akiendell", aliases=["the dell", "my desktop"])
        with patch(
            "unseen_university.devices.igor.tools.machine_manager.get_ranked_machines", return_value=[m]
        ):
            self.assertEqual(resolve_alias("akiendell"), "akiendell")

    def test_alias_match(self):
        from unseen_university.devices.igor.tools.machine_manager import resolve_alias

        m = _machine(hostname="akiendell", aliases=["the dell", "my desktop"])
        with patch(
            "unseen_university.devices.igor.tools.machine_manager.get_ranked_machines", return_value=[m]
        ):
            self.assertEqual(resolve_alias("the dell"), "akiendell")
            self.assertEqual(resolve_alias("MY DESKTOP"), "akiendell")

    def test_no_match_returns_none(self):
        from unseen_university.devices.igor.tools.machine_manager import resolve_alias

        m = _machine(hostname="akiendell", aliases=["the dell"])
        with patch(
            "unseen_university.devices.igor.tools.machine_manager.get_ranked_machines", return_value=[m]
        ):
            self.assertIsNone(resolve_alias("yoga"))


# ── cluster_router.route ──────────────────────────────────────────────────────


class TestRoute(unittest.TestCase):
    """Tests for cluster_router.route — mocks get_ranked_machines and _is_ollama_healthy."""

    def _route(self, machines, healthy_hosts=None, env_override=""):
        """Run route("tier2") with mocked machine list and health."""
        from unseen_university.devices.igor.cognition import cluster_router

        if healthy_hosts is None:
            # All online machines are healthy by default
            healthy_hosts = {m.ollama_host for m in machines if m.status == "online"}

        def _fake_healthy(host):
            return host in healthy_hosts

        def _fake_in_use(hostname):
            m = next((x for x in machines if x.hostname == hostname), None)
            if m is None:
                return False
            # Canonical impl lives in devices.igor.tools.machine_manager; patch
            # there so internal get_machine / _write_override lookups resolve.
            from unseen_university.devices.igor.tools.machine_manager import is_in_use as _real

            with patch(
                "unseen_university.devices.igor.tools.machine_manager.get_machine", return_value=m
            ):
                with patch("unseen_university.devices.igor.tools.machine_manager._write_override"):
                    return _real(hostname)

        with patch(
            "igor.cognition.cluster_router.get_ranked_machines", return_value=machines
        ):
            with patch(
                "igor.cognition.cluster_router._is_ollama_healthy",
                side_effect=_fake_healthy,
            ):
                with patch(
                    "igor.cognition.cluster_router.is_in_use", side_effect=_fake_in_use
                ):
                    with patch.dict(
                        "os.environ", {"IGOR_INFERENCE_OVERRIDE": env_override}
                    ):
                        return cluster_router.route("tier2")

    def test_returns_first_available(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        host, model = self._route(machines)
        self.assertEqual(host, "http://10.0.0.1:11434")

    def test_skips_in_use_machine(self):
        hour = datetime.now().hour
        machines = [
            _machine(
                hostname="a",
                ip="10.0.0.1",
                inference_rank=1,
                in_use_hours=[[hour, (hour + 1) % 24]] if hour < 23 else [],
            ),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        if datetime.now().hour < 23:
            host, model = self._route(machines)
            self.assertEqual(host, "http://10.0.0.2:11434")

    def test_skips_unhealthy_machine(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        host, model = self._route(machines, healthy_hosts={"http://10.0.0.2:11434"})
        self.assertEqual(host, "http://10.0.0.2:11434")

    def test_returns_none_none_when_all_down(self):
        machines = [_machine(hostname="a", ip="10.0.0.1", inference_rank=1)]
        host, model = self._route(machines, healthy_hosts=set())
        self.assertIsNone(host)
        self.assertIsNone(model)

    def test_override_reorders_machines(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        host, model = self._route(machines, env_override="b")
        self.assertEqual(host, "http://10.0.0.2:11434")

    def test_offline_machine_skipped(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1, status="offline"),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        host, model = self._route(machines)
        self.assertEqual(host, "http://10.0.0.2:11434")

    def test_extraction_uses_single_local_model(self):
        # 2026-04-18: post-collapse, extraction resolves to ollama_model
        # (the single local model on the machine), regardless of whether
        # an ollama_model_batch happens to be set on older rows.
        from unseen_university.devices.igor.cognition import cluster_router

        machines = [
            _machine(
                hostname="a",
                ip="10.0.0.1",
                inference_rank=1,
                ollama_model="qwen2.5:7b",
                ollama_model_batch="qwen2.5:14b",  # legacy field, ignored
            ),
        ]
        with patch(
            "igor.cognition.cluster_router.get_ranked_machines", return_value=machines
        ):
            with patch(
                "igor.cognition.cluster_router._is_ollama_healthy", return_value=True
            ):
                with patch(
                    "igor.cognition.cluster_router.is_in_use", return_value=False
                ):
                    host, model = cluster_router.route("extraction")
        self.assertEqual(model, "qwen2.5:7b")


# ── route_batch ───────────────────────────────────────────────────────────────


class TestRouteBatch(unittest.TestCase):
    def _route_batch(self, machines, n, healthy_hosts=None):
        from unseen_university.devices.igor.cognition import cluster_router

        if healthy_hosts is None:
            healthy_hosts = {m.ollama_host for m in machines if m.status == "online"}

        with patch(
            "igor.cognition.cluster_router.get_ranked_machines", return_value=machines
        ):
            with patch(
                "igor.cognition.cluster_router._is_ollama_healthy",
                side_effect=lambda h: h in healthy_hosts,
            ):
                with patch(
                    "igor.cognition.cluster_router.is_in_use", return_value=False
                ):
                    return cluster_router.route_batch(n, "extraction")

    def test_returns_up_to_n(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
            _machine(hostname="c", ip="10.0.0.3", inference_rank=3),
        ]
        result = self._route_batch(machines, 2)
        self.assertEqual(len(result), 2)

    def test_excludes_unhealthy(self):
        machines = [
            _machine(hostname="a", ip="10.0.0.1", inference_rank=1),
            _machine(hostname="b", ip="10.0.0.2", inference_rank=2),
        ]
        result = self._route_batch(machines, 5, healthy_hosts={"http://10.0.0.2:11434"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "http://10.0.0.2:11434")

    def test_n_zero_returns_empty(self):
        machines = [_machine(hostname="a", ip="10.0.0.1", inference_rank=1)]
        result = self._route_batch(machines, 0)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
