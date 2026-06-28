"""
Tests for the pre-execution policy gate in BaseShim.

Completion criteria (from T-shim-policy-gate):
  - a tool call from an agent without a valid provenance token is denied and logged
  - a tool call exceeding budget is denied
  - a tool call not in allowed_actions is denied
  - all denials appear in decision log with reason
  - allowed calls proceed with <5ms added latency
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from unseen_university.devices.policy.gate import PolicyGate, _write_governance_decision
from unseen_university.shim import AgentContext, BaseShim, PolicyDeniedError

# ── Minimal concrete shim for testing ────────────────────────────────────────


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

    def slow_op(self) -> str:
        return "slow"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def trace_dir(tmp_path: Path) -> Path:
    d = tmp_path / "trace"
    d.mkdir()
    return d


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    d = tmp_path / "policies"
    d.mkdir()
    # Write a permissive policy for "igor"
    (d / "igor.yaml").write_text('agent_type: igor\nallowed_actions:\n  - "*"\n')
    # Write a restrictive policy for "restricted_agent"
    (d / "restricted_agent.yaml").write_text(
        "agent_type: restricted\nallowed_actions:\n  - echo\n"
    )
    return d


@pytest.fixture(autouse=True)
def _isolate_env(trace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UU_SHIM_TRACE_DIR", str(trace_dir))


def _governance_records(trace_dir: Path) -> list[dict]:
    """Return all records from governance_*.jsonl in trace_dir."""
    records = []
    for f in trace_dir.glob("governance_*.jsonl"):
        for line in f.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── PolicyGate.check() unit tests ────────────────────────────────────────────


class TestPolicyGateProvenance:
    def test_invalid_token_denied(self, policy_dir: Path) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
        )
        allowed, reason = gate.check("igor", "echo", token="bad-token")
        assert not allowed
        assert "provenance" in reason.lower()

    def test_valid_token_passes_provenance(self, policy_dir: Path) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: True,
        )
        allowed, _ = gate.check("igor", "echo", token="good-token")
        assert allowed

    def test_none_token_denied_when_provenance_wired(self, policy_dir: Path) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: t is not None,
        )
        allowed, reason = gate.check("igor", "echo", token=None)
        assert not allowed
        assert "provenance" in reason.lower()

    def test_no_verify_token_skips_provenance_check(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, _ = gate.check("igor", "echo", token=None)
        assert allowed


class TestPolicyGateAllowedActions:
    def test_action_in_wildcard_allowed(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, _ = gate.check("igor", "any_tool_name", token=None)
        assert allowed

    def test_action_in_explicit_list_allowed(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, _ = gate.check("restricted_agent", "echo", token=None)
        assert allowed

    def test_action_not_in_list_denied(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, reason = gate.check(
            "restricted_agent", "delete_everything", token=None
        )
        assert not allowed
        assert "allowed_actions" in reason or "delete_everything" in reason

    def test_no_policy_file_denied(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, reason = gate.check("unknown_agent", "echo", token=None)
        assert not allowed
        assert "no policy" in reason


class TestPolicyGateBudget:
    def test_budget_exhausted_denied(self, policy_dir: Path) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_budget=lambda: (False, "budget exhausted: $0.00 remaining"),
        )
        allowed, reason = gate.check("igor", "echo", token=None)
        assert not allowed
        assert "budget" in reason.lower() or "exhausted" in reason.lower()

    def test_budget_ok_allows(self, policy_dir: Path) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_budget=lambda: (True, "OK ($10.00 remaining)"),
        )
        allowed, _ = gate.check("igor", "echo", token=None)
        assert allowed

    def test_no_budget_checker_skips_check(self, policy_dir: Path) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        allowed, _ = gate.check("igor", "echo", token=None)
        assert allowed


class TestPolicyGateCheckOrder:
    def test_provenance_checked_before_allowed_actions(self, policy_dir: Path) -> None:
        """Provenance failure should deny before reaching allowed_actions check."""
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
        )
        allowed, reason = gate.check("restricted_agent", "echo", token="bad")
        assert not allowed
        assert "provenance" in reason.lower()

    def test_allowed_actions_checked_before_budget(self, policy_dir: Path) -> None:
        """allowed_actions failure should deny before reaching budget check."""
        budget_called = []
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_budget=lambda: (budget_called.append(True), (True, "ok"))[-1],
        )
        gate.check("restricted_agent", "forbidden_op", token=None)
        assert not budget_called, "budget should not have been checked"


# ── Governance log tests ──────────────────────────────────────────────────────


class TestGovernanceLog:
    def test_denial_written_to_governance_log(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        gate.check("restricted_agent", "forbidden_op", token=None)
        records = _governance_records(trace_dir)
        assert len(records) == 1
        r = records[0]
        assert r["verdict"] == "deny"
        assert r["agent_id"] == "restricted_agent"
        assert r["action"] == "forbidden_op"
        assert "reason" in r and r["reason"]
        assert "ts" in r
        assert "policy_checked" in r

    def test_allow_written_to_governance_log(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        gate = PolicyGate(policies_dir=policy_dir)
        gate.check("igor", "echo", token=None)
        records = _governance_records(trace_dir)
        assert len(records) == 1
        assert records[0]["verdict"] == "allow"

    def test_provenance_denial_includes_policy_checked(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
        )
        gate.check("igor", "echo", token="bad")
        records = _governance_records(trace_dir)
        assert "provenance" in records[0]["policy_checked"]

    def test_budget_denial_includes_budget_in_policy_checked(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        gate = PolicyGate(
            policies_dir=policy_dir,
            check_budget=lambda: (False, "out of credits"),
        )
        gate.check("igor", "echo", token=None)
        records = _governance_records(trace_dir)
        assert "budget" in records[0]["policy_checked"]


# ── BaseShim.dispatch() integration ──────────────────────────────────────────


class TestDispatchPolicyGate:
    def test_no_policy_context_skips_gate(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        shim = _StubShim()
        gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,  # would deny if gate runs
        )
        shim._policy_gate = gate
        # No _policy kwarg → gate not consulted, call succeeds
        result = shim.dispatch("echo", msg="hi")
        assert result == "echo:hi"

    def test_no_gate_wired_fails_closed(self, trace_dir: Path) -> None:
        shim = _StubShim()
        # _policy_gate is None → cold-start fail-closed: deny any _policy call
        ctx = AgentContext(agent_id="nobody", token=None)
        with pytest.raises(PolicyDeniedError) as exc_info:
            shim.dispatch("echo", _policy=ctx, msg="hi")
        assert exc_info.value.agent_id == "nobody"
        assert "cold start" in exc_info.value.reason.lower()

    def test_invalid_token_raises_policy_denied_error(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
        )
        ctx = AgentContext(agent_id="igor", token="bad-token")
        with pytest.raises(PolicyDeniedError) as exc_info:
            shim.dispatch("echo", _policy=ctx, msg="hi")
        assert exc_info.value.action == "echo"
        assert exc_info.value.agent_id == "igor"
        assert "provenance" in exc_info.value.reason.lower()

    def test_forbidden_action_raises_policy_denied_error(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        shim = _StubShim()
        shim._policy_gate = PolicyGate(policies_dir=policy_dir)
        ctx = AgentContext(agent_id="restricted_agent", token=None)
        with pytest.raises(PolicyDeniedError) as exc_info:
            shim.dispatch("slow_op", _policy=ctx)
        assert exc_info.value.action == "slow_op"

    def test_budget_exceeded_raises_policy_denied_error(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            check_budget=lambda: (False, "balance $0.00 at floor"),
        )
        ctx = AgentContext(agent_id="igor", token=None)
        with pytest.raises(PolicyDeniedError) as exc_info:
            shim.dispatch("echo", _policy=ctx, msg="hi")
        assert "balance" in exc_info.value.reason

    def test_allowed_call_executes_and_returns_result(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: t == "valid-token",
        )
        ctx = AgentContext(agent_id="igor", token="valid-token")
        result = shim.dispatch("echo", _policy=ctx, msg="hello")
        assert result == "echo:hello"

    def test_denial_not_written_to_call_trace(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        """Denied calls must NOT appear in the call-log (YYYYMMDD.jsonl)."""
        shim = _StubShim()
        shim._policy_gate = PolicyGate(
            policies_dir=policy_dir,
            verify_token=lambda t: False,
        )
        ctx = AgentContext(agent_id="igor", token="bad")
        with pytest.raises(PolicyDeniedError):
            shim.dispatch("echo", _policy=ctx, msg="hi")
        # Call-log files are YYYYMMDD.jsonl (no "governance_" prefix)
        call_logs = list(trace_dir.glob("[0-9]*.jsonl"))
        assert call_logs == [], "denied call must not appear in call log"

    def test_allowed_call_latency_under_5ms(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        """Gate overhead on warmed cache must be <5ms."""
        shim = _StubShim()
        shim._policy_gate = PolicyGate(policies_dir=policy_dir)
        ctx = AgentContext(agent_id="igor", token=None)
        # Warm the cache
        shim.dispatch("echo", _policy=ctx, msg="warmup")

        # Measure gate overhead across several calls
        REPS = 10
        t0 = time.monotonic()
        for _ in range(REPS):
            shim.dispatch("echo", _policy=ctx, msg="x")
        elapsed_ms = (time.monotonic() - t0) * 1000 / REPS
        assert elapsed_ms < 5.0, f"gate overhead {elapsed_ms:.2f}ms exceeds 5ms target"

    def test_dispatch_kwargs_not_swallowed_by_policy_param(
        self, trace_dir: Path, policy_dir: Path
    ) -> None:
        """Ensure _policy kwarg is consumed by dispatch, not forwarded to the tool."""
        shim = _StubShim()
        shim._policy_gate = PolicyGate(policies_dir=policy_dir)
        ctx = AgentContext(agent_id="igor", token=None)
        # echo() only accepts 'msg' — if _policy leaked into **kwargs, it would TypeError
        result = shim.dispatch("echo", _policy=ctx, msg="clean")
        assert result == "echo:clean"
