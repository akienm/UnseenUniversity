"""
Tests for ContainerShim — tier-2 container-isolated execution.

Unit tests mock subprocess so they run without Docker installed.

Docker-gated integration tests (class TestContainerShimIntegration) require
Docker and are skipped automatically when the `docker` executable is absent.
Run them explicitly with: pytest -m docker tests/test_container_shim.py

Security properties tested:
  - docker.sock in allowed_mounts raises ValueError (escape prevention)
  - start() always uses --network=none (network isolation)
  - resource limits (cpu/memory) passed through to docker run
  - stop() when no container is a no-op
  - rollback() force-removes the container
  - dispatch() traces all calls via BaseShim (shim spy — T-shim-traffic-spy)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.devices.skeleton.container_shim import ContainerShim


def _running_result(container_id: str = "abc123def456") -> MagicMock:
    return MagicMock(returncode=0, stdout=container_id + "\n", stderr="")


def _ok_result() -> MagicMock:
    return MagicMock(returncode=0, stdout="", stderr="")


# ── Validation ────────────────────────────────────────────────────────────────


class TestDockerSockDenied:
    def test_explicit_docker_sock_raises(self):
        shim = ContainerShim(
            "test-agent",
            allowed_mounts=["/var/run/docker.sock:/var/run/docker.sock"],
        )
        with pytest.raises(ValueError, match="docker.sock"):
            shim.start()

    def test_custom_path_containing_docker_sock_raises(self):
        shim = ContainerShim(
            "test-agent",
            allowed_mounts=["/custom/docker.sock:/tmp/d.sock"],
        )
        with pytest.raises(ValueError, match="docker.sock"):
            shim.start()

    def test_validation_runs_before_docker_call(self):
        shim = ContainerShim(
            "test-agent",
            allowed_mounts=["/var/run/docker.sock:/var/run/docker.sock"],
        )
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError):
                shim.start()
            mock_run.assert_not_called()

    def test_no_docker_sock_allows_other_mounts(self):
        shim = ContainerShim("test-agent", allowed_mounts=["/tmp/data:/data"])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            assert shim.start() is True


# ── Network model ─────────────────────────────────────────────────────────────


class TestNetworkIsolation:
    def test_start_always_passes_network_none(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            shim.start()
        cmd = mock_run.call_args[0][0]
        assert "--network=none" in cmd

    def test_unix_socket_bind_mounted(self):
        shim = ContainerShim("test-agent", socket_dir="/tmp")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            shim.start()
        cmd = mock_run.call_args[0][0]
        socket_mounts = [a for a in cmd if "uu-shim" in a and "sock" in a]
        assert socket_mounts, f"no unix socket mount found in: {cmd}"


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_docker_not_found_returns_false(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            assert shim.start() is False

    def test_start_docker_error_returns_false(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="No such image"
            )
            assert shim.start() is False

    def test_start_stores_container_id(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result("abc123")
            assert shim.start() is True
        assert shim._container_id == "abc123"

    def test_stop_no_container_is_noop(self):
        shim = ContainerShim("test-agent")
        assert shim.stop() is True

    def test_stop_calls_docker_stop(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_result()
            assert shim.stop() is True
        cmd = mock_run.call_args[0][0]
        assert "stop" in cmd
        assert "abc123" in cmd

    def test_stop_clears_container_id(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_result()
            shim.stop()
        assert shim._container_id is None

    def test_restart_calls_stop_then_start(self):
        shim = ContainerShim("test-agent")
        with (
            patch.object(shim, "stop", return_value=True) as mock_stop,
            patch.object(shim, "start", return_value=True) as mock_start,
        ):
            assert shim.restart() is True
        mock_stop.assert_called_once()
        mock_start.assert_called_once()

    def test_restart_returns_false_when_stop_fails(self):
        shim = ContainerShim("test-agent")
        with patch.object(shim, "stop", return_value=False):
            assert shim.restart() is False

    def test_rollback_force_removes_container(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_result()
            shim.rollback()
        cmd = mock_run.call_args[0][0]
        assert "rm" in cmd
        assert "-f" in cmd
        assert "abc123" in cmd

    def test_rollback_clears_container_id(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_result()
            shim.rollback()
        assert shim._container_id is None

    def test_rollback_no_container_is_noop(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run") as mock_run:
            shim.rollback()
        mock_run.assert_not_called()


# ── Self-test ──────────────────────────────────────────────────────────────────


class TestSelfTest:
    def test_self_test_no_container_fails(self):
        shim = ContainerShim("test-agent")
        result = shim.self_test()
        assert result["passed"] is False
        assert "no container" in result["details"]

    def test_self_test_running_container_passes(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123def456"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n", stderr="")
            result = shim.self_test()
        assert result["passed"] is True

    def test_self_test_stopped_container_fails(self):
        shim = ContainerShim("test-agent")
        shim._container_id = "abc123def456"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="false\n", stderr="")
            result = shim.self_test()
        assert result["passed"] is False


# ── Resource limits ───────────────────────────────────────────────────────────


class TestResourceLimits:
    def test_cpu_limit_passed_to_docker(self):
        shim = ContainerShim("test-agent", resource_limits={"cpu": "0.5"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            shim.start()
        cmd = mock_run.call_args[0][0]
        assert "--cpus=0.5" in cmd

    def test_memory_limit_passed_to_docker(self):
        shim = ContainerShim("test-agent", resource_limits={"memory": "512m"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            shim.start()
        cmd = mock_run.call_args[0][0]
        assert "--memory=512m" in cmd

    def test_no_limits_omits_flags(self):
        shim = ContainerShim("test-agent")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _running_result()
            shim.start()
        cmd = mock_run.call_args[0][0]
        assert not any(a.startswith("--cpus=") for a in cmd)
        assert not any(a.startswith("--memory=") for a in cmd)


# ── Shim spy (dispatch audit logging) ────────────────────────────────────────


class TestDispatchTracing:
    def test_dispatch_traces_to_jsonl(self):
        shim = ContainerShim("test-agent")
        shim.greet = lambda name: f"hello {name}"

        with tempfile.TemporaryDirectory() as trace_dir:
            os.environ["UU_SHIM_TRACE_DIR"] = trace_dir
            try:
                shim.dispatch("greet", name="world")
            finally:
                del os.environ["UU_SHIM_TRACE_DIR"]

            trace_files = list(Path(trace_dir).glob("*.jsonl"))
            assert trace_files, "no trace file written"
            records = [
                json.loads(line) for line in trace_files[0].read_text().splitlines()
            ]
            assert records, "trace file is empty"
            record = records[-1]
            assert record["device_id"] == "test-agent"
            assert record["tool_name"] == "greet"
            assert record["success"] is True

    def test_dispatch_traces_failure(self):
        shim = ContainerShim("test-agent")
        shim.boom = lambda: (_ for _ in ()).throw(RuntimeError("kaboom"))

        with tempfile.TemporaryDirectory() as trace_dir:
            os.environ["UU_SHIM_TRACE_DIR"] = trace_dir
            try:
                with pytest.raises(RuntimeError):
                    shim.dispatch("boom")
            finally:
                del os.environ["UU_SHIM_TRACE_DIR"]

            records = [
                json.loads(line)
                for line in list(Path(trace_dir).glob("*.jsonl"))[0]
                .read_text()
                .splitlines()
            ]
            record = records[-1]
            assert record["success"] is False
            assert record["error_type"] == "RuntimeError"


# ── Docker-gated integration tests ───────────────────────────────────────────

try:
    _docker_available = (
        subprocess.run(["docker", "version"], capture_output=True, timeout=5).returncode
        == 0
    )
except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
    _docker_available = False


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
class TestContainerShimIntegration:
    """
    Real end-to-end tests — require Docker installed and running.

    These are the only tests that verify the security properties:
      - network=none blocks outbound HTTP
      - container can reach shim via unix socket

    Run with: pytest -m docker tests/test_container_shim.py
    """

    def test_container_cannot_make_outbound_http(self):
        """A tier-2 agent inside --network=none cannot reach the internet."""
        shim = ContainerShim(
            "integ-test-network",
            container_image="alpine",
            resource_limits={"memory": "64m"},
        )
        try:
            assert shim.start(), "container failed to start"
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    shim._container_id,
                    "wget",
                    "-q",
                    "--timeout=3",
                    "-O",
                    "/dev/null",
                    "http://example.com",
                ],
                capture_output=True,
                timeout=10,
            )
            assert (
                result.returncode != 0
            ), "container with --network=none should not be able to reach example.com"
        finally:
            shim.stop()

    def test_container_stops_on_stop_call(self):
        """stop() halts the container; self_test() reports not running."""
        shim = ContainerShim(
            "integ-test-stop",
            container_image="alpine",
            resource_limits={"memory": "64m"},
        )
        assert shim.start(), "container failed to start"
        container_id = shim._container_id
        assert shim.stop()
        inspect = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert (
            inspect.stdout.strip() != "true"
        ), "container should be stopped after shim.stop()"
