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

    def test_claim_next_ticket_returns_ticket_on_success(self):
        d = self._device()
        ticket = {"id": "T-abc", "title": "Fix it", "status": "in_progress", "worker": "dicksimnel"}
        import json
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(ticket))
            result = d._claim_next_ticket()
        assert result is not None
        assert result["id"] == "T-abc"
        assert d._active_ticket == "T-abc"

    def test_claim_next_ticket_returns_none_on_empty(self):
        d = self._device()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="no ticket", stdout="")
            result = d._claim_next_ticket()
        assert result is None
        assert d._active_ticket is None

    def test_poll_and_work_skips_when_blocked(self):
        d = self._device()
        d._blocked = True
        d._claim_next_ticket = MagicMock()
        d._poll_and_work()
        d._claim_next_ticket.assert_not_called()

    def test_poll_and_work_skips_when_active(self):
        d = self._device()
        d._active_ticket = "T-running"
        d._claim_next_ticket = MagicMock()
        d._poll_and_work()
        d._claim_next_ticket.assert_not_called()

    def test_poll_and_work_full_cycle(self):
        d = self._device()
        ticket = {"id": "T-new", "title": "Fix bug", "status": "in_progress",
                  "worker": "dicksimnel", "description": "Fix the thing", "tags": [], "size": "S"}
        d._claim_next_ticket = MagicMock(return_value=ticket)
        d._run_inference = MagicMock(return_value="## Analysis\nFixed it.")
        d._post_result = MagicMock()

        d._poll_and_work()

        d._claim_next_ticket.assert_called_once()
        d._run_inference.assert_called_once_with(ticket)
        d._post_result.assert_called_once()
        assert d._active_ticket is None

    def test_poll_and_work_declines_on_inference_failure(self):
        d = self._device()
        ticket = {"id": "T-fail", "title": "T", "status": "in_progress",
                  "worker": "dicksimnel", "description": "", "tags": [], "size": "S"}
        d._claim_next_ticket = MagicMock(return_value=ticket)
        d._run_inference = MagicMock(return_value=None)
        d._decline_ticket = MagicMock()

        d._poll_and_work()

        d._decline_ticket.assert_called_once()
        assert d._active_ticket is None


# ── skill_load + _build_system_prompt ─────────────────────────────────────────


class TestDickSimnelSkillLoad:
    def _device(self):
        from devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        d._shim = MagicMock()
        d._shim.is_blocked.return_value = False
        return d

    def test_skill_load_returns_content_when_found(self, tmp_path):
        from devices.dicksimnel.device import DickSimnelDevice
        skill_dir = tmp_path / "sprint-ticket"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Sprint Procedure\nStep 1. Do the thing.")
        d = DickSimnelDevice()
        d._shim = MagicMock()
        with patch("devices.dicksimnel.device._SKILLS_DIR", tmp_path):
            content = d.skill_load("sprint-ticket")
        assert content is not None
        assert "Step 1" in content

    def test_skill_load_returns_none_when_missing(self, tmp_path):
        d = self._device()
        with patch("devices.dicksimnel.device._SKILLS_DIR", tmp_path):
            assert d.skill_load("sprint-ticket") is None

    def test_build_system_prompt_includes_skill_when_found(self, tmp_path):
        from devices.dicksimnel.device import DickSimnelDevice
        skill_dir = tmp_path / "sprint-ticket"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("SKILL_CONTENT_MARKER")
        d = DickSimnelDevice()
        d._shim = MagicMock()
        with patch("devices.dicksimnel.device._SKILLS_DIR", tmp_path):
            prompt = d._build_system_prompt({})
        assert "SKILL_CONTENT_MARKER" in prompt
        assert "Sprint Procedure" in prompt

    def test_build_system_prompt_falls_back_to_base_when_skill_missing(self, tmp_path):
        from devices.dicksimnel.device import SYSTEM_PROMPT
        d = self._device()
        with patch("devices.dicksimnel.device._SKILLS_DIR", tmp_path):
            prompt = d._build_system_prompt({})
        assert prompt == SYSTEM_PROMPT

    def test_run_inference_uses_skill_prompt(self, tmp_path):
        from devices.dicksimnel.device import DickSimnelDevice
        skill_dir = tmp_path / "sprint-ticket"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("SKILL_MARKER")
        d = DickSimnelDevice()
        d._shim = MagicMock()

        captured = []
        mock_response = MagicMock()
        mock_response.text = "done"
        mock_response.output_tokens = 10
        mock_response.cost_estimate = 0.001

        def mock_dispatch(req):
            captured.append(req.system)
            return mock_response

        with patch("devices.dicksimnel.device._SKILLS_DIR", tmp_path):
            with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=mock_dispatch):
                d._run_inference({"id": "T-test", "title": "Test", "tags": [], "description": "x"})

        assert captured, "dispatch was not called"
        assert "SKILL_MARKER" in captured[0]
