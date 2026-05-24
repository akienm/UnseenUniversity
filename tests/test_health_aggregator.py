"""Tests for HealthAggregator — T-librarian-health-aggregator."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from bus.imap_server import IMAPServer
from bus.envelope import Envelope
from unseen_university.devices.librarian.health_aggregator import HealthAggregator

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def imap():
    s = IMAPServer()
    s.start()
    s.create_mailbox("heartbeat")
    yield s
    s.stop()


def _send_heartbeat(
    imap, device_id, health="healthy", uptime_s=10.0, current_action=""
):
    env = Envelope.now(
        from_device=device_id,
        to_device="heartbeat",
        payload={
            "device_id": device_id,
            "ts": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
            "uptime_s": uptime_s,
            "health": health,
            "current_action": current_action,
        },
    )
    imap.append("heartbeat", env)


# ── Core ingestion ─────────────────────────────────────────────────────────────


class TestPump:
    def test_pump_updates_last_seen(self, imap):
        agg = HealthAggregator(imap, interval_s=30.0)
        _send_heartbeat(imap, "device-a")
        count = agg.pump()
        assert count == 1
        result = agg.rack_health()
        ids = {d["device_id"] for d in result["devices"]}
        assert "device-a" in ids

    def test_pump_returns_count(self, imap):
        agg = HealthAggregator(imap, interval_s=30.0)
        _send_heartbeat(imap, "device-a")
        _send_heartbeat(imap, "device-b")
        count = agg.pump()
        assert count == 2

    def test_pump_ignores_missing_device_id(self, imap):
        env = Envelope.now(
            from_device="anon",
            to_device="heartbeat",
            payload={"ts": "2026-01-01T00:00:00+00:00", "health": "healthy"},
        )
        imap.append("heartbeat", env)
        agg = HealthAggregator(imap, interval_s=30.0)
        count = agg.pump()
        assert count == 1
        assert agg.rack_health()["devices"] == []

    def test_pump_fetch_failure_returns_zero(self):
        from unittest.mock import MagicMock

        bad_imap = MagicMock()
        bad_imap.fetch_unseen.side_effect = Exception("boom")
        agg = HealthAggregator(bad_imap, interval_s=30.0)
        assert agg.pump() == 0

    def test_current_action_stored(self, imap):
        agg = HealthAggregator(imap, interval_s=30.0)
        _send_heartbeat(imap, "device-a", current_action="processing_queue")
        agg.pump()
        device = agg.rack_health()["devices"][0]
        assert device["current_action"] == "processing_queue"


# ── Silence thresholds ────────────────────────────────────────────────────────


class TestSilenceThresholds:
    def test_fresh_heartbeat_is_healthy(self, imap):
        agg = HealthAggregator(imap, interval_s=30.0)
        # Skip warm-up by injecting a very old started_at
        from datetime import datetime, timezone, timedelta

        agg._started_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        _send_heartbeat(imap, "device-a", health="healthy")
        agg.pump()
        result = agg.rack_health()
        device = next(d for d in result["devices"] if d["device_id"] == "device-a")
        assert device["status"] == "healthy"

    def test_suspect_after_2x_interval(self, imap):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        agg = HealthAggregator(imap, interval_s=30.0)
        agg._started_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        _send_heartbeat(imap, "device-a")
        agg.pump()

        # Advance last_ts to make it look stale (> 60s ago)
        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=70)
        agg._table["device-a"].last_ts = stale_ts

        result = agg.rack_health()
        device = next(d for d in result["devices"] if d["device_id"] == "device-a")
        assert device["status"] == "suspect"

    def test_down_after_3x_interval(self, imap):
        from datetime import datetime, timezone, timedelta

        agg = HealthAggregator(imap, interval_s=30.0)
        agg._started_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        _send_heartbeat(imap, "device-a")
        agg.pump()

        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=100)
        agg._table["device-a"].last_ts = stale_ts

        result = agg.rack_health()
        device = next(d for d in result["devices"] if d["device_id"] == "device-a")
        assert device["status"] == "down"

    def test_warm_up_suppresses_silence_flags(self, imap):
        from datetime import datetime, timezone, timedelta

        agg = HealthAggregator(imap, interval_s=30.0)
        # Don't advance _started_at — still in warm-up window
        _send_heartbeat(imap, "device-a")
        agg.pump()

        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=100)
        agg._table["device-a"].last_ts = stale_ts

        result = agg.rack_health()
        device = next(d for d in result["devices"] if d["device_id"] == "device-a")
        # warm-up: no silence check applied
        assert device["status"] != "down"


# ── IDLE loop ─────────────────────────────────────────────────────────────────


class TestIdleLoop:
    def test_run_forever_processes_heartbeat(self, imap):
        agg = HealthAggregator(imap, interval_s=0.05)
        stop = threading.Event()
        t = threading.Thread(target=agg.run_forever, kwargs={"stop": stop}, daemon=True)
        t.start()

        time.sleep(0.1)
        _send_heartbeat(imap, "device-c")
        time.sleep(0.3)
        stop.set()
        t.join(timeout=1.0)

        ids = {d["device_id"] for d in agg.rack_health()["devices"]}
        assert "device-c" in ids

    def test_run_forever_stops_on_event(self, imap):
        agg = HealthAggregator(imap, interval_s=60.0)
        stop = threading.Event()
        t = threading.Thread(target=agg.run_forever, kwargs={"stop": stop}, daemon=True)
        t.start()
        assert t.is_alive()
        stop.set()
        t.join(timeout=1.0)
        assert not t.is_alive()


# ── rack_health MCP tool ───────────────────────────────────────────────────────


class TestRackHealthTool:
    def test_rack_health_no_aggregator(self):
        from unseen_university.devices.librarian.tools import health_tools

        health_tools._aggregator = None
        result = json.loads(health_tools.rack_health())
        assert "error" in result
        assert result["devices"] == []

    def test_rack_health_with_aggregator(self, imap):
        from unseen_university.devices.librarian.tools import health_tools

        agg = HealthAggregator(imap, interval_s=30.0)
        health_tools.set_aggregator(agg)
        _send_heartbeat(imap, "device-d")
        agg.pump()

        result = json.loads(health_tools.rack_health())
        ids = {d["device_id"] for d in result["devices"]}
        assert "device-d" in ids

        health_tools._aggregator = None  # cleanup

    def test_tool_dispatch_routes(self, imap):
        from unseen_university.devices.librarian.tools import health_tools

        agg = HealthAggregator(imap, interval_s=30.0)
        health_tools.set_aggregator(agg)
        result = health_tools.dispatch("rack_health", {})
        assert result is not None
        health_tools._aggregator = None

    def test_rack_health_in_schema(self):
        from unseen_university.devices.librarian.tools import SCHEMAS

        names = {s["name"] for s in SCHEMAS}
        assert "rack_health" in names
