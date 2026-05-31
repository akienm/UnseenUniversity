"""
PolicyGate — pre-execution allow/deny gate for BaseShim.dispatch().

Evaluated synchronously before every governed tool call. Three checks in order
(first failure wins, check order is intentional per ticket spec):

  1. Provenance: verify_token(token) — caller holds a rack-issued credential
  2. Allowed actions: action must appear in allowed_actions list (or "*")
  3. Budget: check_budget() returns (True, ...) — resources are available

Every decision (allow AND deny) is appended to
datacenter_logs/shim/trace/governance_YYYYMMDD.jsonl —
the decision bill of materials: agent_id, action, policies_checked, verdict,
reason, ts.

This is not a full BaseDevice — it is a lightweight synchronous component
instantiated once at rack startup and injected into each shim via
shim._policy_gate. No I/O after initial YAML load; steady-state must be <5ms.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import yaml  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyYAML required: pip install pyyaml") from exc

log = logging.getLogger(__name__)

_UU_POLICY_DIR_ENV = "UU_POLICY_DIR"
_UU_SHIM_TRACE_DIR_ENV = "UU_SHIM_TRACE_DIR"

DEFAULT_POLICIES_DIR = Path(__file__).parent.parent.parent / "config" / "policies"


def _governance_trace_dir() -> Path:
    env = os.environ.get(_UU_SHIM_TRACE_DIR_ENV)
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "datacenter_logs" / "shim" / "trace"


def _write_governance_decision(record: dict) -> None:
    """Append a governance decision record to the trace file. Never raises."""
    try:
        trace_dir = _governance_trace_dir()
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = trace_dir / f"governance_{date_str}.jsonl"
        with open(log_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("governance log write failed (non-fatal): %s", exc)


class PolicyGate:
    """
    Pre-execution allow/deny gate. Instantiate once per rack startup and inject
    into each shim via shim._policy_gate.

    Args:
        policies_dir:  Directory holding <agent_id>.yaml policy files.
                       Defaults to config/policies/ (or UU_POLICY_DIR env var).
        verify_token:  callable(token) -> bool. None skips provenance check.
        check_budget:  callable() -> (bool, str). None skips budget check.
    """

    def __init__(
        self,
        policies_dir: Path | str | None = None,
        verify_token: Callable | None = None,
        check_budget: Callable | None = None,
    ) -> None:
        if policies_dir is None:
            env_dir = os.environ.get(_UU_POLICY_DIR_ENV)
            self._policies_dir = Path(env_dir) if env_dir else DEFAULT_POLICIES_DIR
        else:
            self._policies_dir = Path(policies_dir)
        self._verify_token = verify_token
        self._check_budget = check_budget
        self._policy_cache: dict[str, list[str]] = {}

    def check(self, agent_id: str, action: str, token: object) -> tuple[bool, str]:
        """
        Evaluate all policies for (agent_id, action, token).

        Returns (allowed, reason). Writes a governance record as a side effect.
        Check order: provenance → allowed_actions → budget. First failure wins.
        """
        policies_checked: list[str] = []

        # 1. Provenance check
        if self._verify_token is not None:
            policies_checked.append("provenance")
            if not self._verify_token(token):
                return self._deny(
                    agent_id, action, policies_checked, "invalid provenance token"
                )

        # 2. Allowed actions check
        policies_checked.append("allowed_actions")
        allowed_actions = self._load_allowed_actions(agent_id)
        if allowed_actions is None:
            return self._deny(
                agent_id,
                action,
                policies_checked,
                f"no policy found for agent {agent_id!r}",
            )
        if "*" not in allowed_actions and action not in allowed_actions:
            return self._deny(
                agent_id,
                action,
                policies_checked,
                f"action {action!r} not in allowed_actions for {agent_id!r}",
            )

        # 3. Budget check
        if self._check_budget is not None:
            policies_checked.append("budget")
            ok, msg = self._check_budget()
            if not ok:
                return self._deny(agent_id, action, policies_checked, msg)

        self._record("allow", agent_id, action, policies_checked, "all checks passed")
        return True, "ok"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_allowed_actions(self, agent_id: str) -> list[str] | None:
        if agent_id in self._policy_cache:
            return self._policy_cache[agent_id]
        policy_path = self._policies_dir / f"{agent_id}.yaml"
        if not policy_path.exists():
            return None
        try:
            data = yaml.safe_load(policy_path.read_text())
            actions = list(data.get("allowed_actions", []))
            self._policy_cache[agent_id] = actions
            return actions
        except Exception as exc:
            log.warning("policy gate: failed to load policy for %r: %s", agent_id, exc)
            return None

    def _deny(
        self,
        agent_id: str,
        action: str,
        policies_checked: list[str],
        reason: str,
    ) -> tuple[bool, str]:
        self._record("deny", agent_id, action, policies_checked, reason)
        return False, reason

    def _record(
        self,
        verdict: str,
        agent_id: str,
        action: str,
        policies_checked: list[str],
        reason: str,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "action": action,
            "policy_checked": policies_checked,
            "verdict": verdict,
            "reason": reason,
        }
        if verdict == "deny":
            log.warning(
                "policy gate: DENY agent=%r action=%r reason=%r",
                agent_id,
                action,
                reason,
            )
        _write_governance_decision(record)
