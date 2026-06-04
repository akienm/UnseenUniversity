"""
IgorShim — BaseShim subclass that wraps a DatacenterClient for an Igor instance.

The datacenter manages many agent types via shims. IgorShim is the rack-side
view of a single Igor instance: it owns lifecycle (install/start/stop/restart)
and exposes Igor's announced capability manifest through a stable shim API
so callers don't have to know about DatacenterClient.

Per G-decision (§ 14): on top of BaseShim, IgorShim adds install + connect +
capability-reading methods.

Slice 3b scope:
  - install(): ensures a runtime copy of the canonical igor.yaml exists in
    profiles_dir (idempotent — safe to re-run).
  - connect(): builds an IdentityEnvelope, instantiates DatacenterClient,
    runs announce(). Raises ConnectionError on timeout (caller retries).
  - capability accessors forward to DatacenterClient; return [] before connect.
  - Lifecycle methods (start/stop/restart/self_test/rollback) are lightweight —
    actual Igor process spawn is left to higher-level orchestration.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from unseen_university.shim import BaseShim

from .client import AnnounceTimeoutError, DatacenterClient

log = logging.getLogger(__name__)

# Where the canonical profiles live (read-only repo copy).
_CANONICAL_PROFILES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "config" / "profiles"
)


class IgorShim(BaseShim):
    """
    Datacenter-side adapter for an Igor instance.

    Args:
        instance_id:  e.g. "wild-0001" — distinguishes one Igor from another.
        profiles_dir: runtime profiles directory (where profile YAMLs live).
        box:          hostname (defaults to socket.gethostname()).
        box_n:        instance number on this box (default 0).
        canonical_profiles_dir: source directory for install() to copy from.
                                Defaults to the repo's config/profiles/.
    """

    def __init__(
        self,
        instance_id: str,
        profiles_dir: Path | str,
        box: str | None = None,
        box_n: int = 0,
        canonical_profiles_dir: Path | str | None = None,
    ) -> None:
        import socket

        self._instance_id = instance_id
        self._profiles_dir = Path(profiles_dir)
        self._box = box or socket.gethostname()
        self._box_n = box_n
        self._canonical_profiles_dir = (
            Path(canonical_profiles_dir)
            if canonical_profiles_dir is not None
            else _CANONICAL_PROFILES_DIR
        )
        self._client: DatacenterClient | None = None
        self._started = False

    # ── BaseShim contract ─────────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        return f"igor-{self._instance_id}"

    def start(self) -> bool:
        """Mark the shim as started. Idempotent — second call is a no-op."""
        self._started = True
        return True

    def stop(self) -> bool:
        """Mark the shim as stopped. Does NOT halt Igor itself in this slice."""
        self._started = False
        return True

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def self_test(self) -> dict:
        if self._client is None:
            return {"passed": False, "details": "not connected to datacenter"}
        if self._client.manifest is None:
            return {"passed": False, "details": "connected but manifest absent"}
        return {
            "passed": True,
            "details": (
                f"manifest cached "
                f"({len(self._client.get_tools())} tools, "
                f"{len(self._client.get_state_refs())} state_refs)"
            ),
        }

    def rollback(self) -> None:
        """Drop the connection. install() is idempotent so nothing else to undo."""
        self._client = None
        self._started = False

    # ── G-decision (§ 14) extensions: install + connect + capability reading ──

    def install(self) -> None:
        """
        Ensure the runtime profile YAML exists in profiles_dir. Copies the
        canonical igor.yaml on first call; subsequent calls are no-ops.
        """
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        runtime = self._profiles_dir / "igor.yaml"
        if runtime.exists():
            log.debug("igor-shim install: %s already present, skipping copy", runtime)
            return
        canonical = self._canonical_profiles_dir / "igor.yaml"
        if not canonical.exists():
            raise FileNotFoundError(f"canonical igor.yaml not found at {canonical}")
        shutil.copy(canonical, runtime)
        log.info("igor-shim install: copied %s → %s", canonical, runtime)

    def connect(self, timeout: float = 5.0, proof: dict | None = None) -> dict:
        """
        Instantiate the DatacenterClient and announce. Returns the manifest dict.
        Raises ConnectionError if announce times out — caller decides retry.
        proof: optional shared-secret dict for protected-agent validation.
        """
        client = DatacenterClient(
            agent_id="igor",
            instance=self._instance_id,
            box=self._box,
            box_n=self._box_n,
            surfaces=["console", "inference"],
            proof=proof or {},
        )
        try:
            manifest = client.announce(timeout=timeout)
        except AnnounceTimeoutError as exc:
            raise ConnectionError(
                f"igor-shim connect: announce timed out for "
                f"{client.identity.primary_mailbox}: {exc}"
            ) from exc
        self._client = client
        return manifest

    @property
    def client(self) -> DatacenterClient | None:
        """The underlying DatacenterClient (None before connect())."""
        return self._client

    @property
    def manifest(self) -> dict | None:
        return self._client.manifest if self._client else None

    def get_tools(self) -> list:
        if self._client is None:
            return []
        return self._client.get_tools()

    def get_tool(self, name: str):
        if self._client is None:
            return None
        return self._client.get_tool(name)

    def get_state_refs(self) -> list:
        if self._client is None:
            return []
        return self._client.get_state_refs()

    def get_state_ref(self, name: str):
        if self._client is None:
            return None
        return self._client.get_state_ref(name)

    def get_channels(self) -> list:
        if self._client is None:
            return []
        return self._client.get_channels()

    def get_primary_address(self) -> str | None:
        if self._client is None:
            return None
        return self._client.get_primary_address()
