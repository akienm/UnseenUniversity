"""Tests for VetinariDevice — meta-orchestrator.

Completion criteria:
- Vetinari can own a factory spec
- Vetinari can receive a health rollup for a factory
- Vetinari escalates to Akien when eval scores drop below threshold
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def _make_vetinari(tmp_path, threshold=0.5):
    import os
    import devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
    from devices.vetinari.device import VetinariDevice
    channel_calls = []
    v = VetinariDevice(
        escalation_threshold=threshold,
        channel_post_fn=lambda msg: channel_calls.append(msg),
    )
    return v, channel_calls


class TestVetinariOwnsFactory:
    def test_own_factory_adds_to_registry(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        v.own_factory("factory-1", {"name": "Sprint Factory", "tier": "worker"})
        owned = v.get_owned_factories()
        assert len(owned) == 1
        assert owned[0]["factory_id"] == "factory-1"
        assert owned[0]["owner_id"] == "comms://vetinari/"

    def test_own_factory_persists_to_disk(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        v.own_factory("factory-2", {"name": "Eval Factory"})
        # Reload from disk
        import os
        import devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
        from devices.vetinari.device import VetinariDevice
        v2 = VetinariDevice(channel_post_fn=lambda m: None)
        owned = v2.get_owned_factories()
        assert any(f["factory_id"] == "factory-2" for f in owned)

    def test_halt_factory_marks_status(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        v.own_factory("factory-halt", {})
        v.halt_factory("factory-halt", "eval score too low")
        owned = {f["factory_id"]: f for f in v.get_owned_factories()}
        assert owned["factory-halt"]["status"] == "halted"
        assert "too low" in owned["factory-halt"]["halt_reason"]


class TestVetinariHealthRollup:
    def test_healthy_rollup_returns_false(self, tmp_path):
        v, channel_calls = _make_vetinari(tmp_path, threshold=0.5)
        v.own_factory("f-1", {})
        escalated = v.receive_health_rollup("f-1", {"eval_score": 0.9, "status": "healthy"})
        assert escalated is False
        assert channel_calls == []

    def test_below_threshold_escalates(self, tmp_path):
        v, channel_calls = _make_vetinari(tmp_path, threshold=0.5)
        v.own_factory("f-critical", {})
        escalated = v.receive_health_rollup(
            "f-critical", {"eval_score": 0.3, "status": "degraded", "detail": "tests failing"}
        )
        assert escalated is True
        assert len(channel_calls) == 1
        assert "VETINARI_ESCALATE" in channel_calls[0]
        assert "f-critical" in channel_calls[0]
        assert "0.300" in channel_calls[0]

    def test_threshold_boundary_escalates_below(self, tmp_path):
        v, channel_calls = _make_vetinari(tmp_path, threshold=0.5)
        v.own_factory("f-boundary", {})
        v.receive_health_rollup("f-boundary", {"eval_score": 0.499})
        assert len(channel_calls) == 1

    def test_threshold_boundary_no_escalate_at_equal(self, tmp_path):
        v, channel_calls = _make_vetinari(tmp_path, threshold=0.5)
        v.own_factory("f-equal", {})
        v.receive_health_rollup("f-equal", {"eval_score": 0.5})
        assert channel_calls == []

    def test_unknown_factory_returns_false(self, tmp_path):
        v, channel_calls = _make_vetinari(tmp_path)
        result = v.receive_health_rollup("not-registered", {"eval_score": 0.1})
        assert result is False
        assert channel_calls == []


class TestVetinariBaseDeviceContract:
    def test_who_am_i(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        info = v.who_am_i()
        assert info["device_id"] == "vetinari"
        assert "meta-orchestrator" in info["purpose"].lower()

    def test_health_healthy_when_no_factories(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        h = v.health()
        assert h["status"] == "healthy"

    def test_health_degraded_when_factory_below_threshold(self, tmp_path):
        v, _ = _make_vetinari(tmp_path, threshold=0.5)
        v.own_factory("f-bad", {})
        v.receive_health_rollup("f-bad", {"eval_score": 0.2})
        h = v.health()
        assert h["status"] == "degraded"
        assert "f-bad" in h["degraded_factories"]

    def test_restart_reloads_registry(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        v.own_factory("f-persist", {})
        v.restart()
        owned = v.get_owned_factories()
        assert any(f["factory_id"] == "f-persist" for f in owned)

    def test_block_and_recovery(self, tmp_path):
        v, _ = _make_vetinari(tmp_path)
        v.block("test block")
        assert v._blocked is True
        v.recovery()
        assert v._blocked is False
