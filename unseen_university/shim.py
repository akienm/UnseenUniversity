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
  to get automatic JSONL logging to ~/.unseen_university/logs/shim/trace/YYYYMMDD.jsonl.
  Override the log directory via UU_SHIM_TRACE_DIR env var (used in tests).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from unseen_university.diagnostic_base.core_values import CoreValuesMixin

_CIRCUIT_STATE_FILE = Path(
    os.environ.get(
        "UU_CIRCUIT_STATE_FILE",
        str(Path.home() / ".unseen_university" / "circuit_state.json"),
    )
)

if TYPE_CHECKING:
    from unseen_university.devices.policy.gate import PolicyGate
    from unseen_university.devices.policy.output_validators import OutputValidator

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Dispatch-time identity context. Pass as _policy to BaseShim.dispatch()."""

    agent_id: str
    token: object = field(default=None)


class ShimLoopThread:
    """A shim-owned background poll loop — the ONE way a device runs periodic work.

    ONE daemon structure (T-collapse-daemons-to-ground-loop): no device runs its own
    ``__main__`` + ``while True`` daemon. A device's periodic work is a daemon thread
    its SHIM owns (the aider pattern, generalized). Each cycle calls ``tick()``; the
    thread body is wrapped in ``logger.contextualize(device_id=...)`` so stdlib records
    emitted from ``tick`` carry ``device_id`` and reach the canonical per-device JSON
    sink (``~/.unseen_university/logs/<device>/<stream>/``) — threads start with an
    empty contextvars context, so the wrap MUST be inside the thread body, not around
    ``Thread(...)``. Run first, then wait (parity with the old ``run_loop`` cadence).

    ``tick`` and ``on_cycle`` exceptions are logged, never fatal — one bad cycle must
    not kill the loop.
    """

    def __init__(self, device_id, tick, interval, *, on_cycle=None, name=None):
        self._device_id = device_id
        self._tick = tick
        self._interval = interval
        self._on_cycle = on_cycle  # optional callable(cycle_number) after each tick
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._name = name or f"{device_id}-loop"

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Idempotent — a watchdog may call this repeatedly; only one thread runs."""
        if self.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from loguru import logger as _loguru_logger

        with _loguru_logger.contextualize():  # STUB: device_id stamp not yet wired
            cycle = 0
            while not self._stop.is_set():
                cycle += 1
                try:
                    self._tick()
                except Exception as exc:
                    log.error("%s: tick cycle %d error: %s", self._name, cycle, exc)
                if self._on_cycle is not None:
                    try:
                        self._on_cycle(cycle)
                    except Exception as exc:
                        log.warning("%s: on_cycle %d error: %s", self._name, cycle, exc)
                self._stop.wait(self._interval)


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
            # Canonical per-device log home (T-per-device-log-hierarchy): the shim's
            # dispatch trace is the "shim" device's log, under logs/shim/, not the
            # retired logs/ root.
            trace_dir = Path.home() / ".unseen_university" / "logs" / "shim" / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = trace_dir / f"{date_str}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("shim trace write failed (non-fatal): %s", exc)


class _DispatchHandshake:
    """Manages the ack→prod→(started|timeout) lifecycle for one dispatch event.

    Protocol (envelope kinds over send_fn):
      1. dispatch_ack     — sent immediately in start(); Granny marks ticket acked
      2. dispatch_started — sent when deliver_fn returns True; Granny marks in_progress
      3. dispatch_timeout — sent after timeout_at seconds with no pickup; Granny escalates

    All three carry {kind, ticket_id, from_device} so Granny can correlate the response.
    """

    PROD_INTERVAL: float = 120.0  # seconds between deliver_fn probes
    TIMEOUT_AT: float = 600.0     # seconds from ack until timeout envelope

    def __init__(
        self,
        ticket_id: str,
        from_device: str,
        device_id: str,
        send_fn,      # (to_device: str, payload: dict) -> None
        deliver_fn,   # (ticket_id: str) -> bool — True means app accepted
        *,
        prod_interval: float = PROD_INTERVAL,
        timeout_at: float = TIMEOUT_AT,
    ) -> None:
        self._ticket_id = ticket_id
        self._from_device = from_device
        self._device_id = device_id
        self._send_fn = send_fn
        self._deliver_fn = deliver_fn
        self._prod_interval = prod_interval
        self._timeout_at = timeout_at
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Send ack synchronously, then launch the background prod loop."""
        self._send_fn(
            self._from_device,
            {
                "kind": "dispatch_ack",
                "ticket_id": self._ticket_id,
                "from_device": self._device_id,
            },
        )
        log.info("dispatch_ack: ticket=%s to=%s", self._ticket_id, self._from_device)
        self._thread = threading.Thread(
            target=self._prod_loop,
            daemon=True,
            name=f"dispatch-{self._ticket_id}",
        )
        self._thread.start()

    def _prod_loop(self) -> None:
        started_at = time.monotonic()
        while not self._stop.is_set():
            # Wait one prod interval; return immediately on cancel
            if self._stop.wait(timeout=self._prod_interval):
                return
            # Try to deliver the work to the app
            if self._deliver_fn(self._ticket_id):
                self._send_fn(
                    self._from_device,
                    {
                        "kind": "dispatch_started",
                        "ticket_id": self._ticket_id,
                        "from_device": self._device_id,
                    },
                )
                log.info(
                    "dispatch_started: ticket=%s to=%s",
                    self._ticket_id,
                    self._from_device,
                )
                self._stop.set()
                return
            # Timeout check comes after a failed delivery attempt
            if time.monotonic() - started_at >= self._timeout_at:
                self._send_fn(
                    self._from_device,
                    {
                        "kind": "dispatch_timeout",
                        "ticket_id": self._ticket_id,
                        "from_device": self._device_id,
                    },
                )
                log.warning(
                    "dispatch_timeout: ticket=%s to=%s",
                    self._ticket_id,
                    self._from_device,
                )
                self._stop.set()
                return

    def cancel(self) -> None:
        """Cancel the handshake (idempotent — safe to call after completion)."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the prod loop thread to exit (used in tests)."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def is_active(self) -> bool:
        """True while the prod loop is still running."""
        return not self._stop.is_set()


class BaseShim(CoreValuesMixin, ABC):
    """Abstract base for all device shims.

    Inherits CP1–CP6 via CoreValuesMixin — every shim carries the core values
    structurally (see diagnostic_base/core_values.py).
    """

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

    # ── Common skill interface ─────────────────────────────────────────────────

    def handle_command(self, cmd: str) -> str:
        """Route a chat command to the appropriate skill handler.

        Commands starting with '/' are dispatched to registered skill handlers.
        Everything else goes to _handle_non_skill() — override for device personality.
        """
        cmd = cmd.strip()
        if cmd.startswith("/"):
            parts = cmd.split(None, 1)
            verb = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            handler = self._skill_handlers().get(verb)
            if handler:
                return handler(args)
            known = ", ".join(sorted(self._skill_handlers()))
            return f"Unknown skill {verb!r}. Known: {known}"
        return self._handle_non_skill(cmd)

    def _skill_handlers(self) -> dict:
        """Return verb → handler mapping. Override to add device-specific skills."""
        return {
            "/help":   self._skill_help,
            "/health": self._skill_health,
            "/stop":   self._skill_stop,
            "/resume": self._skill_resume,
            "/feed":   self._skill_feed,
        }

    def _skill_help(self, args: str = "") -> str:
        verbs = sorted(self._skill_handlers())
        return f"{self.device_id} skills: " + "  ".join(verbs)

    def _skill_health(self, args: str = "") -> str:
        surface = self.health_surface()
        if not surface:
            return f"{self.device_id}: no health data"
        return "\n".join(f"{k}={v}" for k, v in sorted(surface.items()))

    def _skill_stop(self, args: str = "") -> str:
        ok = self.stop()
        return f"{self.device_id}: stopped" if ok else f"{self.device_id}: stop failed"

    def _skill_resume(self, args: str = "") -> str:
        ok = self.start()
        return f"{self.device_id}: resumed" if ok else f"{self.device_id}: resume failed"

    def _skill_feed(self, args: str = "") -> str:
        return f"{self.device_id}: no feed entries"

    def _handle_non_skill(self, text: str) -> str:
        """Handle non-skill input. Override for device personality."""
        return f"{self.device_id}: not a skill — try /help"

    @staticmethod
    def _tokenize(text: str) -> list:
        """Return token-id list for text. Character-ordinal encoding — test utility only."""
        return [ord(c) for c in text]

    # ── Daemon supervision ─────────────────────────────────────────────────────

    @staticmethod
    def spawn_foreground_session(
        session_name: str,
        cmd: list[str],
        *,
        no_attach: bool = False,
    ) -> None:
        """Start cmd in a named tmux session, attaching when a terminal is available.

        With a tty and no_attach=False: exec() into tmux, replacing the caller's
        process with the tmux session. The call does not return on success.

        Without a tty or with no_attach=True: create a detached tmux session and
        send the command to it via send-keys. Returns after the session is created.

        When the session already exists: attach (exec path) or return silently
        (detached path) — a second process is never started.
        """
        import sys as _sys

        session_exists = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        ).returncode == 0

        attach = (not no_attach) and _sys.stdin.isatty()

        if session_exists:
            if attach:
                os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])
            return

        if attach:
            os.execvp(
                "tmux",
                ["tmux", "new-session", "-s", session_name, "-x", "220", "-y", "50", *cmd],
            )
        else:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", session_name, "-x", "220", "-y", "50"],
                check=True,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name, " ".join(cmd), "Enter"],
                check=True,
            )
        log.info("spawn_foreground_session: session=%s detached", session_name)

    def ensure_daemon_running(self) -> bool:
        """Check if the device's backing daemon is alive; start it if not.

        Default: no-op — returns True. Override in shims that supervise an
        external daemon process. The shim's watchdog calls this on every poll
        cycle so the device is never 'down' as long as its shim is running.
        """
        return True

    def health_surface(self) -> dict[str, str]:
        """Return key/value health status pairs for this device.

        Default returns the dynamic cache populated by _post_status().
        Override to add static fields:
            def health_surface(self):
                return {**super().health_surface(), "my_field": "value"}
        """
        return dict(self.__dict__.get("_health_cache_store", {}))

    def check_circuit(self) -> bool:
        """Return True if this device's circuit breaker is OPEN (paused).

        Reads circuit_state.json (UU_CIRCUIT_STATE_FILE). Returns False when
        the file doesn't exist or this device's entry is absent or CLOSED.
        """
        try:
            data = json.loads(_CIRCUIT_STATE_FILE.read_text())
            state = data.get(self.device_id, "CLOSED")
            log.debug("circuit check: device=%s state=%s", self.device_id, state)
            return state == "OPEN"
        except FileNotFoundError:
            return False
        except Exception as exc:
            log.debug("circuit check failed (non-fatal): %s", exc)
            return False

    def _post_status(self, key: str, value: str) -> None:
        """Cache a status key=value and post it to this device's channel.

        Updates the health_surface() cache and posts to channel so the web
        pane can show TTL-aged status without polling the device directly.
        """
        try:
            self._health_cache_store[key] = str(value)
        except AttributeError:
            self._health_cache_store: dict[str, str] = {key: str(value)}
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(
                f"{key}={value}", author=self.device_id, channel=self.device_id
            )
        except Exception as exc:
            log.debug("_post_status channel write failed (non-fatal): %s", exc)

    _output_validator: OutputValidator | None = None
    _policy_gate: PolicyGate | None = None
    _notifier: "NotificationDispatcher | None" = None  # type: ignore[name-defined]

    def filter_incoming(self, sender: str, summary: str) -> "DeliveryMode":  # type: ignore[name-defined]
        """Deliver an incoming message through the notification filter.

        When _notifier is set, delegates to NotificationDispatcher.deliver()
        which applies config + state-linked defaults and executes delivery
        (SILENT/QUIET/LOUD). When _notifier is not set, returns QUIET and
        logs the message — default behaviour before a shim wires its notifier.

        Call this in the device's message-receive loop instead of handling
        delivery ad-hoc. Logs at INFO for every message (AR-009).
        """
        if self._notifier is not None:
            from .notify import DeliveryMode
            return self._notifier.deliver(sender, summary)
        # No notifier configured — log and treat as QUIET (message in mailbox)
        from .notify import DeliveryMode
        log.info("notif: %s → QUIET (reason: no-notifier-configured)", sender)
        return DeliveryMode.QUIET

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

        Writes to ~/.unseen_university/logs/shim/trace/YYYYMMDD.jsonl (or
        UU_SHIM_TRACE_DIR if set). Log write is fire-and-forget — a failure
        never blocks the tool call.
        """
        if _policy is not None:
            if self._policy_gate is None:
                # Policy device not yet initialized — fail closed (cold start window).
                # Allowing tool calls before policy is ready would bypass the entire
                # governance gate. Log and deny until policy device signals ready.
                from unseen_university.devices.policy.gate import _write_governance_decision
                _write_governance_decision({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "agent_id": _policy.agent_id,
                    "action": tool_name,
                    "policy_checked": ["coldstart_failclosed"],
                    "verdict": "deny",
                    "reason": "policy device not ready — shim fail-closed during cold start",
                    "device_id": self.device_id,
                })
                log.warning(
                    "shim cold-start deny: policy gate not ready — "
                    "denying %r from agent %r on %s",
                    tool_name, _policy.agent_id, self.device_id,
                )
                raise PolicyDeniedError(
                    tool_name,
                    "policy device not ready (cold start — shim fails closed)",
                    _policy.agent_id,
                )
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

    # ── Dispatch handshake ─────────────────────────────────────────────────────

    def receive_dispatch(
        self,
        envelope,
        *,
        send_fn,
        deliver_fn=None,
        prod_interval: float = _DispatchHandshake.PROD_INTERVAL,
        timeout_at: float = _DispatchHandshake.TIMEOUT_AT,
    ) -> "_DispatchHandshake":
        """Handle an incoming dispatch envelope from the bus.

        Immediately sends a dispatch_ack envelope back to the sender, then
        starts a background prod loop that calls deliver_fn every prod_interval
        seconds. When deliver_fn returns True the shim sends dispatch_started
        and stops. If timeout_at seconds elapse with no pickup, the shim sends
        dispatch_timeout and stops.

        envelope must have .from_device and .payload["ticket_id"] (bus.Envelope)
        or the equivalent dict keys ({"from_device": ..., "payload": {...}}).

        send_fn(to_device, payload) — bus send is injected so BaseShim stays
        transport-agnostic. In production the caller wires this to a bus
        append via bus/ (PgBus); in tests it can be a list-append spy.

        deliver_fn(ticket_id) -> bool — True when the app has accepted the
        work. Default: always returns True (started fires after first prod).

        Returns the _DispatchHandshake so callers can cancel on shutdown.
        """
        if hasattr(envelope, "from_device"):
            from_device = envelope.from_device
            ticket_id = envelope.payload["ticket_id"]
        else:
            from_device = envelope["from_device"]
            ticket_id = envelope["payload"]["ticket_id"]

        if deliver_fn is None:
            deliver_fn = lambda tid: True  # noqa: E731

        hs = _DispatchHandshake(
            ticket_id=ticket_id,
            from_device=from_device,
            device_id=self.device_id,
            send_fn=send_fn,
            deliver_fn=deliver_fn,
            prod_interval=prod_interval,
            timeout_at=timeout_at,
        )
        hs.start()

        if not hasattr(self, "_active_handshakes"):
            self._active_handshakes: dict[str, _DispatchHandshake] = {}
        self._active_handshakes[ticket_id] = hs
        log.info(
            "dispatch received: ticket=%s from=%s — ack sent",
            ticket_id,
            from_device,
        )
        return hs

    def _cancel_active_handshakes(self) -> None:
        """Cancel all in-flight dispatch handshakes and clear the registry.

        Call from stop() to ensure prod loops don't outlive the shim.
        Safe to call multiple times (idempotent).
        """
        handshakes = getattr(self, "_active_handshakes", {})
        for hs in list(handshakes.values()):
            hs.cancel()
        handshakes.clear()
