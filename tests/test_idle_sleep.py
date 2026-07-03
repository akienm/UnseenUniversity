"""
Tests for idle-sleep functionality — DICKSIMNEL_IDLE_TIMEOUT_S and quit_if_idle messages.

Proof nodes:
  - test_busy_instance_never_self_exits: ANCHOR
  - test_idle_instance_self_exits: ANCHOR
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from unseen_university.devices.dicksimnel.worker_listener import DickSimnelWorkerListener
from unseen_university.devices.pool import InstancePool


class FakeEnvelope:
    """Minimal envelope with .payload and .from_device for testing."""

    def __init__(self, payload=None, from_device="test"):
        self.payload = payload or {}
        self.from_device = from_device


class TestIdleSleepProofNodes:
    """Proof nodes for idle-sleep safety."""

    def test_busy_instance_never_self_exits(self):
        """PROOF: Busy instance (with _active_ticket set) NEVER self-exits when quit_if_idle arrives.

        This is the anti-hollow anchor: a build that ignores the busy guard would
        incorrectly call on_idle_shutdown even when the instance is working.
        """
        # Setup: fake device with _active_ticket set (busy)
        fake_device = Mock()
        fake_device._active_ticket = "T-busy-ticket"

        # Fake bus returning one quit_if_idle envelope
        fake_bus = Mock()
        fake_bus.fetch_unseen = Mock(return_value=[
            FakeEnvelope(
                payload={"kind": "quit_if_idle", "idle_timeout": 120},
                from_device="dicksimnel-frontdoor"
            )
        ])

        # Setup listener
        on_idle_shutdown_spy = Mock()
        listener = DickSimnelWorkerListener(
            bus=fake_bus,
            device=fake_device,
            on_idle_shutdown=on_idle_shutdown_spy,
        )
        listener.start()
        time.sleep(0.1)  # Let start() set _last_active

        # Set the idle clock far in the past (so idle timeout WOULD trigger if not for busy guard)
        listener._last_active = time.monotonic() - 9999

        # Poll once — should see quit_if_idle but NOT call on_idle_shutdown (busy guard)
        listener._poll_once()

        # ASSERT: on_idle_shutdown was NOT called because instance is busy
        on_idle_shutdown_spy.assert_not_called()

        listener.stop()

    def test_idle_instance_self_exits(self):
        """PROOF: Idle instance (no _active_ticket, past timeout) DOES self-exit on quit_if_idle."""
        # Setup: fake device with _active_ticket = None (idle)
        fake_device = Mock()
        fake_device._active_ticket = None

        # Fake bus returning one quit_if_idle envelope
        fake_bus = Mock()
        fake_bus.fetch_unseen = Mock(return_value=[
            FakeEnvelope(
                payload={"kind": "quit_if_idle", "idle_timeout": 120},
                from_device="dicksimnel-frontdoor"
            )
        ])

        # Setup listener
        on_idle_shutdown_spy = Mock()
        listener = DickSimnelWorkerListener(
            bus=fake_bus,
            device=fake_device,
            on_idle_shutdown=on_idle_shutdown_spy,
        )
        listener.start()
        time.sleep(0.1)

        # Set the idle clock far in the past (past the 120s timeout)
        listener._last_active = time.monotonic() - 9999

        # Poll once — should see quit_if_idle and SHOULD call on_idle_shutdown
        listener._poll_once()

        # ASSERT: on_idle_shutdown WAS called because instance is idle
        on_idle_shutdown_spy.assert_called_once()

        listener.stop()

    def test_quit_co_arriving_with_dispatch_ignored(self):
        """PROOF: quit_if_idle co-arriving with dispatch in same batch is ignored.

        The dispatch_seen guard ensures that if work just arrived (dispatch in batch),
        the instance is not idle and quit should be ignored.
        """
        # Setup: fake device
        fake_device = Mock()
        fake_device._active_ticket = None

        # Fake bus returning BOTH dispatch and quit_if_idle in one batch
        fake_bus = Mock()
        fake_bus.fetch_unseen = Mock(return_value=[
            FakeEnvelope(
                payload={"kind": "dispatch", "ticket_id": "T-work"},
                from_device="granny.0"
            ),
            FakeEnvelope(
                payload={"kind": "quit_if_idle", "idle_timeout": 120},
                from_device="dicksimnel-frontdoor"
            )
        ])

        # Setup listener (mock _handle_dispatch so it doesn't actually run inference)
        on_idle_shutdown_spy = Mock()
        listener = DickSimnelWorkerListener(
            bus=fake_bus,
            device=fake_device,
            on_idle_shutdown=on_idle_shutdown_spy,
        )
        listener._handle_dispatch = Mock()  # Mock away the actual dispatch handling
        listener.start()
        time.sleep(0.1)

        # Set idle clock far in past (would trigger if not for dispatch_seen guard)
        listener._last_active = time.monotonic() - 9999

        # Poll once — batch has dispatch and quit
        listener._poll_once()

        # ASSERT: on_idle_shutdown was NOT called (dispatch_seen guard prevented it)
        on_idle_shutdown_spy.assert_not_called()

        listener.stop()


class TestIdleSleepNonProofTests:
    """Additional tests (not proof nodes)."""

    def test_cull_dead_reclaims_and_preserves_handles(self):
        """cull_dead() marks dead processes as None and preserves live handles."""
        # Create a fake liveness check that marks index 1 as dead
        def fake_liveness(pid, create_time):
            # Slot 0 (pid=1000) is alive; slot 1 (pid=2000) is dead
            return pid == 1000

        # Setup pool with two slots
        pool = InstancePool(
            "TestClass",
            liveness=fake_liveness,
            home="/tmp/test_pool",
        )

        # Manually populate slots with handles (simulate allocated instances)
        handle_0 = Mock(name="handle_0")
        handle_1 = Mock(name="handle_1")
        pool._slots = [
            {"pid": 1000, "create_time": None, "handle": handle_0},
            {"pid": 2000, "create_time": None, "handle": handle_1},
        ]

        # cull_dead() — should mark slot 1 as None
        reclaimed = pool.cull_dead()

        # ASSERT: slot 0 is live (handle preserved), slot 1 is None
        assert len(pool._slots) == 1
        assert pool._slots[0]["handle"] is handle_0
        assert reclaimed == [1]

    def test_idle_timeout_default_from_env(self):
        """DickSimnelWorkerListener reads DICKSIMNEL_IDLE_TIMEOUT_S from environment."""
        import os

        # Save original
        original = os.environ.get("DICKSIMNEL_IDLE_TIMEOUT_S")

        try:
            # Set custom timeout
            os.environ["DICKSIMNEL_IDLE_TIMEOUT_S"] = "999"

            listener = DickSimnelWorkerListener()

            # ASSERT: timeout was read from env
            assert listener._idle_timeout_s == 999.0

        finally:
            # Restore
            if original is not None:
                os.environ["DICKSIMNEL_IDLE_TIMEOUT_S"] = original
            else:
                os.environ.pop("DICKSIMNEL_IDLE_TIMEOUT_S", None)

    def test_listener_initializes_last_active_at_start(self):
        """listener.start() initializes _last_active to current monotonic time."""
        fake_device = Mock()
        fake_bus = Mock()
        fake_bus.fetch_unseen = Mock(return_value=[])

        listener = DickSimnelWorkerListener(bus=fake_bus, device=fake_device)

        # Before start, _last_active should be None
        assert listener._last_active is None

        # Start listener
        listener.start()
        time.sleep(0.05)  # Give thread time to run

        # After start, _last_active should be set
        assert listener._last_active is not None
        assert isinstance(listener._last_active, float)

        listener.stop()

    def test_quit_if_idle_payload_defaults_to_listener_timeout(self):
        """quit_if_idle without idle_timeout field uses listener's default."""
        fake_device = Mock()
        fake_device._active_ticket = None

        # Envelope with quit_if_idle but no idle_timeout field
        fake_bus = Mock()
        fake_bus.fetch_unseen = Mock(return_value=[
            FakeEnvelope(
                payload={"kind": "quit_if_idle"},  # No idle_timeout
                from_device="dicksimnel-frontdoor"
            )
        ])

        on_idle_shutdown_spy = Mock()
        listener = DickSimnelWorkerListener(
            bus=fake_bus,
            device=fake_device,
            on_idle_shutdown=on_idle_shutdown_spy,
            poll_interval=0.01,
        )
        listener._idle_timeout_s = 50.0  # Set default
        listener.start()
        time.sleep(0.05)

        # Set _last_active far in past (past default timeout)
        listener._last_active = time.monotonic() - 9999

        # Poll once — should use default timeout
        listener._poll_once()

        # ASSERT: on_idle_shutdown was called (defaulted to listener timeout)
        on_idle_shutdown_spy.assert_called_once()

        listener.stop()
