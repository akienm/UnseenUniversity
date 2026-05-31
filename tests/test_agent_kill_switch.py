"""
Tests for T-agent-kill-switch — generalized agent halt/resume via kill switch.

Completion criteria:
  - agent_halt('test-agent', 'reason') causes all subsequent tool calls from
    test-agent to be denied
  - agent_resume restores normal operation
  - halt state persists across rack restart (flat-file registry)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devices.policy.gate import PolicyGate
from skeleton.halt_registry import HaltRegistry
from unseen_university.shim import AgentContext, BaseShim, PolicyDeniedError
from unseen_university.skeleton.skeleton import Skeleton
from skeleton.registry import DeviceRegistry

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")


# ── Fixtures ──────────────────────────────────────────────────────────────────


class _StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass

    def echo(self, msg: str = "") -> str:
        return f"echo:{msg}"


@pytest.fixture()
def trace_dir(tmp_path: Path) -> Path:
    d = tmp_path / "trace"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _isolate_env(trace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UU_SHIM_TRACE_DIR", str(trace_dir))


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    d = tmp_path / "policies"
    d.mkdir()
    (d / "igor.yaml").write_text("agent_type: igor\nallowed_actions:\n  - '*'\n")
    return d


@pytest.fixture()
def halt_registry(tmp_path: Path) -> HaltRegistry:
    return HaltRegistry(path=tmp_path / "registry" / "halted.json")


def _governance_records(trace_dir: Path) -> list[dict]:
    records = []
    for f in trace_dir.glob("governance_*.jsonl"):
        for line in f.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── HaltRegistry unit tests ───────────────────────────────────────────────────


class TestHaltRegistry:
    def test_agent_not_halted_by_default(self, halt_registry: HaltRegistry) -> None:
        halted, _ = halt_registry.is_halted("test-agent")
        assert not halted

    def test_set_halted_marks_agent(self, halt_registry: HaltRegistry) -> None:
        halt_registry.set_halted("test-agent", True, "bad behaviour")
        halted, reason = halt_registry.is_halted("test-agent")
        assert halted
        assert reason == "bad behaviour"

    def test_set_halted_false_clears(self, halt_registry: HaltRegistry) -> None:
        halt_registry.set_halted("test-agent", True, "reason")
        halt_registry.set_halted("test-agent", False)
        halted, _ = halt_registry.is_halted("test-agent")
        assert not halted

    def test_halt_persists_across_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "registry" / "halted.json"
        reg1 = HaltRegistry(path=path)
        reg1.set_halted("igor", True, "violated safety constraint")

        reg2 = HaltRegistry(path=path)
        halted, reason = reg2.is_halted("igor")
        assert halted
        assert reason == "violated safety constraint"

    def test_resume_persists_across_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "registry" / "halted.json"
        reg1 = HaltRegistry(path=path)
        reg1.set_halted("igor", True, "test")
        reg1.set_halted("igor", False)

        reg2 = HaltRegistry(path=path)
        halted, _ = reg2.is_halted("igor")
        assert not halted

    def test_unknown_agent_not_halted(self, halt_registry: HaltRegistry) -> None:
        halted, _ = halt_registry.is_halted("never-registered")
        assert not halted


# ── PolicyGate halt check tests ───────────────────────────────────────────────


class TestPolicyGateHaltCheck:
    def test_halted_agent_denied_before_policy(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        halt_registry.set_halted("igor", True, "kill switch test")
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        allowed, reason = gate.check("igor", "echo", token=None)
        assert not allowed
        assert "halted" in reason.lower()
        assert "kill switch test" in reason

    def test_not_halted_agent_proceeds(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        allowed, _ = gate.check("igor", "echo", token=None)
        assert allowed

    def test_halt_check_before_provenance(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        """Halt check must fire even when provenance would also deny."""
        halt_registry.set_halted("igor", True, "priority test")
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
            check_halted=halt_registry.is_halted,
        )
        allowed, reason = gate.check("igor", "echo", token="bad")
        assert not allowed
        assert "halted" in reason.lower()
        assert "provenance" not in reason.lower()

    def test_halt_denial_written_to_governance_log(
        self, trace_dir: Path, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        halt_registry.set_halted("test-agent", True, "logged halt")
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        gate.check("test-agent", "some_tool", token=None)
        records = _governance_records(trace_dir)
        assert len(records) == 1
        r = records[0]
        assert r["verdict"] == "deny"
        assert r["agent_id"] == "test-agent"
        assert "halt" in r["policy_checked"]

    def test_resume_restores_allow(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        halt_registry.set_halted("igor", True, "temp halt")
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        allowed_before, _ = gate.check("igor", "echo", token=None)
        assert not allowed_before

        halt_registry.set_halted("igor", False)
        allowed_after, _ = gate.check("igor", "echo", token=None)
        assert allowed_after


# ── BaseShim.dispatch() integration ──────────────────────────────────────────


class TestDispatchHaltEnforcement:
    def test_halted_agent_dispatch_raises(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        halt_registry.set_halted("test-agent", True, "dispatch test")
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        ctx = AgentContext(agent_id="test-agent", token=None)
        with pytest.raises(PolicyDeniedError) as exc_info:
            shim.dispatch("echo", _policy=ctx, msg="hi")
        assert "halted" in exc_info.value.reason.lower()
        assert exc_info.value.agent_id == "test-agent"

    def test_resume_restores_dispatch(
        self, policy_dir: Path, halt_registry: HaltRegistry
    ) -> None:
        halt_registry.set_halted("igor", True, "temp")
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            check_halted=halt_registry.is_halted,
        )
        ctx = AgentContext(agent_id="igor", token=None)
        with pytest.raises(PolicyDeniedError):
            shim.dispatch("echo", _policy=ctx, msg="hi")

        halt_registry.set_halted("igor", False)
        result = shim.dispatch("echo", _policy=ctx, msg="hi")
        assert result == "echo:hi"


# ── Skeleton MCP tools ────────────────────────────────────────────────────────


class TestSkeletonKillSwitchTools:
    def _make_skeleton(self, tmp_path: Path) -> tuple[Skeleton, HaltRegistry]:
        registry = DeviceRegistry(path=tmp_path / "devices.json")
        halt_reg = HaltRegistry(path=tmp_path / "registry" / "halted.json")
        skel = Skeleton(registry=registry, halt_registry=halt_reg)
        return skel, halt_reg

    def test_agent_halt_tool_marks_halted(self, tmp_path: Path) -> None:
        skel, halt_reg = self._make_skeleton(tmp_path)
        # Call agent_halt via the registered MCP tool function directly
        result = skel._mcp._tool_manager._tools["agent_halt"].fn(
            agent_id="test-agent", reason="bad actor", from_device="skeleton"
        )
        assert result["ok"] is True
        halted, reason = halt_reg.is_halted("test-agent")
        assert halted
        assert reason == "bad actor"

    def test_agent_resume_tool_clears_halt(self, tmp_path: Path) -> None:
        skel, halt_reg = self._make_skeleton(tmp_path)
        halt_reg.set_halted("test-agent", True, "pre-halted")
        skel._mcp._tool_manager._tools["agent_resume"].fn(
            agent_id="test-agent", from_device="skeleton"
        )
        halted, _ = halt_reg.is_halted("test-agent")
        assert not halted

    def test_agent_halt_requires_skeleton_caller(self, tmp_path: Path) -> None:
        from unseen_university.skeleton.exceptions import AuthError

        skel, _ = self._make_skeleton(tmp_path)
        with pytest.raises(AuthError):
            skel._mcp._tool_manager._tools["agent_halt"].fn(
                agent_id="test-agent", reason="x", from_device="imposter"
            )

    def test_agent_resume_requires_skeleton_caller(self, tmp_path: Path) -> None:
        from unseen_university.skeleton.exceptions import AuthError

        skel, halt_reg = self._make_skeleton(tmp_path)
        halt_reg.set_halted("test-agent", True, "test")
        with pytest.raises(AuthError):
            skel._mcp._tool_manager._tools["agent_resume"].fn(
                agent_id="test-agent", from_device="test-agent"
            )

    def test_halt_governance_record_written(
        self, tmp_path: Path, trace_dir: Path
    ) -> None:
        skel, _ = self._make_skeleton(tmp_path)
        skel._mcp._tool_manager._tools["agent_halt"].fn(
            agent_id="test-agent", reason="audit test", from_device="skeleton"
        )
        records = _governance_records(trace_dir)
        halt_records = [r for r in records if r.get("action") == "agent_halt"]
        assert len(halt_records) == 1
        r = halt_records[0]
        assert r["agent_id"] == "test-agent"
        assert r["verdict"] == "halt"
        assert r["reason"] == "audit test"
