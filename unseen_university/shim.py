"""
BaseShim — device lifecycle and translation layer.

The shim sits between the rack and a device's native interface. It owns:
  - Lifecycle: start, stop, restart, rollback
  - Self-test: verify the device is actually working
  - Translation: converts native errors/states into rack-understood signals
  - Call tracing: dispatch() logs every inter-agent tool call to a JSONL file

One shim per device. The rack calls the shim's lifecycle methods during
registration, health rollup, and restart-loop management.

Call tracing:
  Call shim.dispatch("tool_name", **kwargs) instead of shim.tool_name(**kwargs)
  to get automatic JSONL logging to datacenter_logs/shim/trace/YYYYMMDD.jsonl.
  Override the log directory via UU_SHIM_TRACE_DIR env var (used in tests).
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devices.policy.gate import PolicyGate
    from devices.policy.output_validators import OutputValidator

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Dispatch-time identity context. Pass as _policy to BaseShim.dispatch()."""

    agent_id: str
    token: object = field(default=None)


class PolicyDeniedError(Exception):
    """Raised by dispatch() when the policy gate denies a tool call.

    Shape: action, reason, agent_id available as attributes.
    """

    def __init__(self, action: str, reason: str, agent_id: str = "") -> None:
        self.action = action
        self.reason = reason
        self.agent_id = agent_id
        super().__init__(
            f"Policy denied: agent={agent_id!r} action={action!r} — {reason}"
        )


def _write_shim_trace(record: dict) -> None:
    """Append one JSONL record to the shim trace log. Never raises."""
    try:
        trace_dir_env = os.environ.get("UU_SHIM_TRACE_DIR")
        if trace_dir_env:
            trace_dir = Path(trace_dir_env)
        else:
            trace_dir = (
                Path(__file__).parent.parent / "datacenter_logs" / "shim" / "trace"
            )
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = trace_dir / f"{date_str}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("shim trace write failed (non-fatal): %s", exc)


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

    def ensure_daemon_running(self) -> bool:
        """Check if the device's backing daemon is alive; start it if not.

        Default: no-op — returns True. Override in shims that supervise an
        external daemon process. The shim's watchdog calls this on every poll
        cycle so the device is never 'down' as long as its shim is running.
        """
        return True

    _output_validator: OutputValidator | None = None
    _policy_gate: PolicyGate | None = None

    def validate_output(self, result: object) -> object:
        """Validate and redact sensitive content from a tool result.

        Non-string results pass through unchanged. When _output_validator is
        set, runs string results through the validator and logs each incident
        at WARNING. Returns the (possibly redacted) result.
        """
        if self._output_validator is None or not isinstance(result, str):
            return result
        redacted, incidents = self._output_validator.validate(result)
        for incident in incidents:
            log.warning("output validation: %s [device=%s]", incident, self.device_id)
        return redacted

    def dispatch(
        self, tool_name: str, *, _policy: AgentContext | None = None, **kwargs
    ) -> object:
        """Route a tool call through this shim and write a JSONL trace record.

        When _policy is provided and _policy_gate is set, evaluates all three
        governance checks (provenance, allowed_actions, budget) before dispatch.
        Raises PolicyDeniedError on any denial — denial is NOT recorded in the
        call-log, only in the governance log written by the gate.

        Writes to datacenter_logs/shim/trace/YYYYMMDD.jsonl (or
        UU_SHIM_TRACE_DIR if set). Log write is fire-and-forget — a failure
        never blocks the tool call.
        """
        if _policy is not None and self._policy_gate is not None:
            allowed, reason = self._policy_gate.check(
                _policy.agent_id, tool_name, _policy.token
            )
            if not allowed:
                raise PolicyDeniedError(tool_name, reason, _policy.agent_id)

        t0 = time.monotonic()
        error_type: str | None = None
        success = False
        try:
            result = getattr(self, tool_name)(**kwargs)
            success = True
            return result
        except AttributeError:
            error_type = "AttributeError"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            latency_ms = round((time.monotonic() - t0) * 1000, 2)
            _write_shim_trace(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "device_id": self.device_id,
                    "tool_name": tool_name,
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_type": error_type,
                }
            )
