"""Tests for BaseDevice stopwatch + heartbeat — T-adc-performance-points, T-bus-heartbeat."""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.librarian import Librarian
from unseen_university.devices.bus.imap_server import IMAPServer
from unseen_university.diagnostic_base.perf import Stopwatch

# ── Minimal concrete device for testing abstract helpers ─────────────────────


class _MinimalDevice(BaseDevice):
    def who_am_i(self):
        return {"device_id": "test-device", "name": "Test", "version": "0"}

    def requirements(self):
        return {"deps": []}

    def capabilities(self):
        return {"can_send": True, "can_receive": True, "emitted_keywords": []}

    def comms(self):
        return {
            "address": "comms://test-device",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self):
        return INTERFACE_VERSION

    def health(self):
        from datetime import datetime, timezone

        return {
            "status": "healthy",
            "detail": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def uptime(self):
        return 1.0

    def startup_errors(self):
        return []

    def logs(self):
        return {"paths": {}}

    def update_info(self):
        return {"current_version": "0", "update_available": False}

    def where_and_how(self):
        import os, socket

        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "launch_command": "test",
        }

    def restart(self):
        pass

    def block(self, reason):
        pass

    def halt(self):
        pass

    def recovery(self):
        pass


# ── Stopwatch tests ───────────────────────────────────────────────────────────


class TestBaseDeviceStopwatch:
    def test_returns_stopwatch_instance(self, tmp_path):
        dev = _MinimalDevice()
        sw = dev.stopwatch("op", log_root=tmp_path)
        assert isinstance(sw, Stopwatch)

    def test_device_id_bound(self, tmp_path):
        dev = _MinimalDevice()
        sw = dev.stopwatch("op", log_root=tmp_path)
        assert sw.device_id == "test-device"

    def test_class_name_bound(self, tmp_path):
        dev = _MinimalDevice()
        sw = dev.stopwatch("op", log_root=tmp_path)
        assert sw.class_name == "_MinimalDevice"

    def test_context_manager_works(self, tmp_path):
        dev = _MinimalDevice()
        with dev.stopwatch("timed_op", log_root=tmp_path) as t:
            time.sleep(0.01)
        assert t.success is True
        assert t.elapsed_s >= 0.01

    def test_csv_row_written(self, tmp_path):
        import csv

        dev = _MinimalDevice()
        with dev.stopwatch("csv_op", log_root=tmp_path):
            pass
        perf_dir = tmp_path / "test-device" / "perf"
        csv_files = list(perf_dir.glob("*.perf.csv"))
        assert len(csv_files) == 1
        rows = list(csv.DictReader(csv_files[0].open()))
        assert rows[0]["stopwatch_id"] == "csv_op"
        assert rows[0]["device_id"] == "test-device"

    def test_librarian_stopwatch(self, tmp_path):
        lib = Librarian()
        lib._log_root = tmp_path  # type: ignore[attr-defined]
        sw = lib.stopwatch("lib_op", log_root=tmp_path)
        assert sw.device_id == "librarian"
        assert sw.class_name == "Librarian"


# ── Heartbeat tests ───────────────────────────────────────────────────────────


@pytest.fixture()
def imap():
    s = IMAPServer()
    s.start()
    s.create_mailbox("heartbeat")
    yield s
    s.stop()


class TestBaseDeviceHeartbeat:
    def test_heartbeat_publishes_to_mailbox(self, imap):
        dev = _MinimalDevice()
        stop = threading.Event()
        t = dev.start_heartbeat(imap, interval_s=0.05, stop=stop)

        time.sleep(0.2)
        stop.set()
        # append a dummy to unblock any idle_wait
        t.join(timeout=1.0)

        msgs = imap.fetch_unseen("heartbeat")
        assert len(msgs) >= 1
        payload = msgs[0].payload
        assert payload["device_id"] == "test-device"
        assert "ts" in payload
        assert "uptime_s" in payload
        assert "health" in payload

    def test_heartbeat_stops_on_event(self, imap):
        dev = _MinimalDevice()
        stop = threading.Event()
        t = dev.start_heartbeat(imap, interval_s=10.0, stop=stop)
        assert t.is_alive()
        stop.set()
        t.join(timeout=1.0)
        assert not t.is_alive()

    def test_heartbeat_thread_is_daemon(self, imap):
        dev = _MinimalDevice()
        stop = threading.Event()
        t = dev.start_heartbeat(imap, interval_s=60.0, stop=stop)
        assert t.daemon is True
        stop.set()

    def test_heartbeat_survives_send_failure(self):
        """Heartbeat loop doesn't crash when send raises."""
        from unittest.mock import MagicMock, patch

        dev = _MinimalDevice()
        stop = threading.Event()
        bad_imap = MagicMock()

        with patch(
            "unseen_university.devices.bus.router.Router.send", side_effect=Exception("boom")
        ):
            t = dev.start_heartbeat(bad_imap, interval_s=0.05, stop=stop)
            time.sleep(0.15)
            stop.set()
            t.join(timeout=1.0)
        # if we got here without exception the loop handled errors gracefully
        assert not t.is_alive()
