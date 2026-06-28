"""
ContainerShim — tier-2 execution environment for untrusted agents.

The container IS the security perimeter. The shim runs on the host, holds all
rack access, and enforces the policy gate. The agent runs inside a container
with no host network (--network=none) and can only reach the rack through a
Unix domain socket that the shim binds.

Network model (T-container-shim-network-spec):
  --network=none + Unix domain socket. The container gets no TCP stack at all,
  so it cannot reach host services (Postgres, IMAP) directly. The only channel
  in or out is the bind-mounted Unix socket at /var/run/uu-shim.sock inside
  the container. Bridge networking is explicitly rejected: a bridge gateway
  exposes host services to containers even when the host services bind to
  0.0.0.0.

Security invariant: docker.sock is never mountable (raises ValueError on
start) — mounting it grants full Docker API access = container escape.

All tool calls routed through dispatch() are traced automatically to
logs/shim/trace/YYYYMMDD.jsonl via BaseShim.dispatch().

Lifecycle: start() on announce, stop() on deregister. The rack caller is
responsible for wiring these events; ContainerShim does not hook into the
announce protocol itself.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_DEFAULT_IMAGE = "uu-agent-base"
_SOCKET_CONTAINER_PATH = "/var/run/uu-shim.sock"


class ContainerShim(BaseShim):
    """
    Tier-2 shim: wraps an untrusted agent in a Docker container.

    Args:
        device_id:        Unique rack address for this agent.
        container_image:  Docker image to run (default: uu-agent-base).
        network_policy:   Must be "none"; any other value is documented but
                          the implementation always enforces --network=none.
        allowed_mounts:   Host:container bind mounts. docker.sock is never
                          allowed — ValueError raised in start().
        resource_limits:  Dict with optional keys: cpu (float str, e.g. "0.5"),
                          memory (str, e.g. "512m"), disk (ignored in v1 —
                          Docker disk limiting requires specific storage drivers).
        socket_dir:       Directory for the host-side Unix socket. Defaults to
                          a fresh tempdir per-instance (cleaned up on stop).
    """

    def __init__(
        self,
        device_id: str,
        container_image: str = _DEFAULT_IMAGE,
        network_policy: str = "none",
        allowed_mounts: list[str] | None = None,
        resource_limits: dict | None = None,
        socket_dir: str | None = None,
    ) -> None:
        self._device_id_val = device_id
        self._container_image = container_image
        self._network_policy = network_policy
        self._allowed_mounts: list[str] = list(allowed_mounts or [])
        self._resource_limits: dict = dict(resource_limits or {})
        self._socket_dir = socket_dir
        self._container_id: str | None = None
        self._temp_socket_dir: tempfile.TemporaryDirectory | None = None  # type: ignore[type-arg]

    @property
    def device_id(self) -> str:
        return self._device_id_val

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_mounts(self) -> None:
        for mount in self._allowed_mounts:
            if "docker.sock" in mount:
                raise ValueError(
                    f"ContainerShim: docker.sock is not allowed in allowed_mounts "
                    f"(mount={mount!r}). Mounting the Docker socket grants full "
                    f"Docker API access and trivially escapes the container."
                )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        self._validate_mounts()

        # Resolve host-side socket directory.
        if self._socket_dir:
            socket_host_path = f"{self._socket_dir}/uu-shim-{self._device_id_val}.sock"
        else:
            self._temp_socket_dir = tempfile.TemporaryDirectory(
                prefix=f"uu-shim-{self._device_id_val}-"
            )
            socket_host_path = f"{self._temp_socket_dir.name}/uu-shim.sock"

        cmd = [
            "docker",
            "run",
            "--network=none",  # no TCP stack — unix socket is the only channel
            "--rm",
            "-d",
            f"--name=uu-{self._device_id_val}",
            f"-v={socket_host_path}:{_SOCKET_CONTAINER_PATH}",
        ]

        cpu = self._resource_limits.get("cpu")
        if cpu:
            cmd.append(f"--cpus={cpu}")
        memory = self._resource_limits.get("memory")
        if memory:
            cmd.append(f"--memory={memory}")

        for mount in self._allowed_mounts:
            cmd.extend(["-v", mount])

        cmd.append(self._container_image)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            log.error(
                "ContainerShim[%s]: docker not found — Docker must be installed for tier-2 agents",
                self._device_id_val,
            )
            self._cleanup_temp_dir()
            return False
        except subprocess.TimeoutExpired:
            log.error("ContainerShim[%s]: docker run timed out", self._device_id_val)
            self._cleanup_temp_dir()
            return False

        if result.returncode != 0:
            log.error(
                "ContainerShim[%s]: docker run failed (rc=%d): %s",
                self._device_id_val,
                result.returncode,
                result.stderr.strip(),
            )
            self._cleanup_temp_dir()
            return False

        self._container_id = result.stdout.strip()
        log.info(
            "ContainerShim[%s]: started container %s (image=%s, network=none)",
            self._device_id_val,
            self._container_id[:12],
            self._container_image,
        )
        return True

    def stop(self) -> bool:
        if not self._container_id:
            self._cleanup_temp_dir()
            return True

        try:
            result = subprocess.run(
                ["docker", "stop", self._container_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            log.error(
                "ContainerShim[%s]: docker stop timed out for %s",
                self._device_id_val,
                self._container_id[:12],
            )
            return False

        if result.returncode != 0:
            log.warning(
                "ContainerShim[%s]: docker stop failed (rc=%d): %s",
                self._device_id_val,
                result.returncode,
                result.stderr.strip(),
            )
            return False

        log.info(
            "ContainerShim[%s]: stopped container %s",
            self._device_id_val,
            self._container_id[:12],
        )
        self._container_id = None
        self._cleanup_temp_dir()
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        if not self._container_id:
            return {"passed": False, "details": "no container running"}

        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format={{.State.Running}}",
                    self._container_id,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "details": "docker inspect timed out"}
        except FileNotFoundError:
            return {"passed": False, "details": "docker not found"}

        running = result.stdout.strip() == "true"
        return {
            "passed": running,
            "details": f"container {self._container_id[:12]} running={running}",
        }

    def rollback(self) -> None:
        if self._container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
            self._container_id = None
        self._cleanup_temp_dir()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _cleanup_temp_dir(self) -> None:
        if self._temp_socket_dir is not None:
            try:
                self._temp_socket_dir.cleanup()
            except Exception:
                pass
            self._temp_socket_dir = None

    @property
    def socket_path(self) -> str | None:
        """Host-side Unix socket path, or None if not started."""
        if self._socket_dir:
            return f"{self._socket_dir}/uu-shim-{self._device_id_val}.sock"
        if self._temp_socket_dir:
            return f"{self._temp_socket_dir.name}/uu-shim.sock"
        return None
