"""
BaseShim — device lifecycle and translation layer.

The shim sits between the rack and a device's native interface. It owns:
  - Lifecycle: start, stop, restart, rollback
  - Self-test: verify the device is actually working
  - Translation: converts native errors/states into rack-understood signals

One shim per device. The rack calls the shim's lifecycle methods during
registration, health rollup, and restart-loop management.
"""

from abc import ABC, abstractmethod


class BaseShim(ABC):
    """Abstract base for all device shims."""

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Unique identifier for the device this shim manages."""

    @abstractmethod
    def start(self) -> bool:
        """
        Start the device. Returns True on success, False on failure.
        On failure the rack will call rollback() before retrying.
        """

    @abstractmethod
    def stop(self) -> bool:
        """
        Stop the device gracefully. Returns True on success.
        Called by rack on planned shutdown or block().
        """

    @abstractmethod
    def restart(self) -> bool:
        """
        Restart the device. Returns True on success.
        The rack calls this after a restart-loop failure if not blocked.
        Implementations may delegate to stop() + start().
        """

    @abstractmethod
    def self_test(self) -> dict:
        """
        Verify the device is actually working.
        Return shape: {passed: bool, details: str}.
        Called by rack during registration and periodic health checks.
        """

    @abstractmethod
    def rollback(self) -> None:
        """
        Called when start() returns False. Undo any partial setup.
        Must be idempotent — safe to call even if start() did nothing.
        """
