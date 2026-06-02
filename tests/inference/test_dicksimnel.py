"""Tests for DickSimnelDevice — availability flag, poll loop, inference integration."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── DickSimnelShim ────────────────────────────────────────────────────────────


class TestDickSimnelShim:
    def test_start_writes_availability_flag(self, tmp_path, monkeypatch):
        from devices.dicksimnel.shim import DickSimnelShim, _FLAG_DIR, _AVAILABLE_FLAG

        monkeypatch.setattr("devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("devices.dicksimnel.shim._AVAILABLE_FLAG", tmp_path / "DickSimnel.0.available.true")

        shim = DickSimnelShim()
        assert shim.start()
        assert (tmp_path / "DickSimnel.0.available.true").exists()
        shim.stop()

    def test_stop_removes_flag(self, tmp_path, monkeypatch):
        from devices.dicksimnel.shim import DickSimnelShim

        flag = tmp_path / "DickSimnel.0.available.true"
        monkeypatch.setattr("devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("devices.dicksimnel.shim._AVAILABLE_FLAG", flag)

        shim = DickSimnelShim()
        shim.start()
        assert flag.exists()
        shim.stop()
        assert not flag.exists()

    def test_is_blocked_reads_false_flag(self, tmp_path, monkeypatch):
        from devices.dicksimnel.shim import DickSimnelShim

        blocked_flag = tmp_path / "DickSimnel.0.available.false"
        monkeypatch.setattr("devices.dicksimnel.shim._BLOCKED_FLAG", blocked_flag)

        shim = DickSimnelShim()
        assert not shim.is_blocked()
        blocked_flag.write_text("false")
        assert shim.is_blocked()

    def test_worker_callback_called_by_poll(self, tmp_path, monkeypatch):
        from devices.dicksimnel import shim as shim_mod

        monkeypatch.setattr(shim_mod, "_FLAG_DIR", tmp_path)
        monkeypatch.setattr(shim_mod, "_AVAILABLE_FLAG", tmp_path / "av.true")
        monkeypatch.setattr(shim_mod, "_BLOCKED_FLAG", tmp_path / "av.false")
        monkeypatch.setattr(shim_mod, "_POLL_INTERVAL_S", 0.05)

        called = threading.Event()
        def _cb():
            called.set()

        s = shim_mod.DickSimnelShim(worker_callback=_cb)
        s.start()
        called.wait(timeout=1.0)
        s.stop()
        assert called.is_set()

    def test_self_test_passes_when_inference_importable(self, tmp_path, monkeypatch):
        from devices.dicksimnel.shim import DickSimnelShim

        monkeypatch.setattr("devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        shim = DickSimnelShim()
        result = shim.self_test()
        assert result["passed"]

    def test_rollback_removes_flag_if_written(self, tmp_path, monkeypatch):
        from devices.dicksimnel.shim import DickSimnelShim

        flag = tmp_path / "DickSimnel.0.available.true"
        monkeypatch.setattr("devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("devices.dicksimnel.shim._AVAILABLE_FLAG", flag)

        shim = DickSimnelShim()
        shim._write_available()
        assert flag.exists()
        shim.rollback()
        assert not flag.exists()


# ── DickSimnelDevice ──────────────────────────────────────────────────────────


class TestDickSimnelDevice:
    def _device(self):
        from devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        # Replace shim with a no-op so tests don't touch filesystem or threads
        d._shim = MagicMock()
        d._shim.self_test.return_value = {"passed": True, "details": "mock"}
        d._shim.is_blocked.return_value = False
        return d

    def test_who_am_i(self):
        d = self._device()
        info = d.who_am_i()
        assert info["device_id"] == "dicksimnel"
        assert "worker" in info["agent_class"]

    def test_health_healthy(self):
        d = self._device()
        h = d.health()
        assert h["status"] == "healthy"

    def test_health_blocked(self):
        d = self._device()
        d.block("test block")
        assert d.health()["status"] == "unhealthy"
        assert "test block" in d.health()["detail"]

    def test_find_next_ticket_returns_sprint_ticket(self):
        d = self._device()
        tickets = [
            {"id": "T-done", "status": "done", "worker": "dicksimnel"},
            {"id": "T-work", "status": "sprint", "worker": "dicksimnel", "title": "Fix something"},
        ]
        d._run_queue_cmd = MagicMock(return_value=tickets)
        found = d._find_next_ticket()
        assert found is not None
        assert found["id"] == "T-work"

    def test_find_next_skips_active_ticket(self):
        d = self._device()
        d._active_ticket = "T-work"
        tickets = [
            {"id": "T-work", "status": "sprint", "worker": "dicksimnel"},
        ]
        d._run_queue_cmd = MagicMock(return_value=tickets)
        assert d._find_next_ticket() is None

    def test_find_next_skips_non_sprint(self):
        d = self._device()
        tickets = [
            {"id": "T-triage", "status": "triage", "worker": "dicksimnel"},
            {"id": "T-hold", "status": "hold", "worker": "dicksimnel"},
        ]
        d._run_queue_cmd = MagicMock(return_value=tickets)
        assert d._find_next_ticket() is None

    def test_claim_ticket_sets_active(self):
        d = self._device()
        d._run_queue_cmd = MagicMock(return_value={"ok": True})
        assert d._claim_ticket("T-abc")
        assert d._active_ticket == "T-abc"

    def test_poll_and_work_skips_when_blocked(self):
        d = self._device()
        d._blocked = True
        d._find_next_ticket = MagicMock()
        d._poll_and_work()
        d._find_next_ticket.assert_not_called()

    def test_poll_and_work_skips_when_active(self):
        d = self._device()
        d._active_ticket = "T-running"
        d._find_next_ticket = MagicMock()
        d._poll_and_work()
        d._find_next_ticket.assert_not_called()

    def test_poll_and_work_full_cycle(self):
        d = self._device()
        ticket = {"id": "T-new", "title": "Fix bug", "status": "sprint",
                  "worker": "dicksimnel", "description": "Fix the thing", "tags": [], "size": "S"}
        d._find_next_ticket = MagicMock(return_value=ticket)
        d._claim_ticket = MagicMock(return_value=True)
        d._run_inference = MagicMock(return_value="## Analysis\nFixed it.")
        d._post_result = MagicMock()

        d._poll_and_work()

        d._claim_ticket.assert_called_once_with("T-new")
        d._run_inference.assert_called_once_with(ticket)
        d._post_result.assert_called_once()
        assert d._active_ticket is None  # cleared after work

    def test_poll_and_work_declines_on_inference_failure(self):
        d = self._device()
        ticket = {"id": "T-fail", "title": "T", "status": "sprint",
                  "worker": "dicksimnel", "description": "", "tags": [], "size": "S"}
        d._find_next_ticket = MagicMock(return_value=ticket)
        d._claim_ticket = MagicMock(return_value=True)
        d._run_inference = MagicMock(return_value=None)
        d._decline_ticket = MagicMock()

        d._poll_and_work()

        d._decline_ticket.assert_called_once()
        assert d._active_ticket is None
