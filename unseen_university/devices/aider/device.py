"""
AiderDevice — bus-dispatched autonomous builder that runs aider on a coding ticket.

Aider is a builder-tier worker (same tier as DickSimnel), but instead of DS's
inference-domain loop it wraps the EXTERNAL aider CLI via runner.build() — proven
to make our local models edit+orient where the DS loop produced 0 edits
(project_aider_builder_viable). aider is never imported; runner.py shells to it.

On dispatch (via AiderWorkerListener):
  1. dispatch_ack to Granny
  2. Fetch ticket, decline HIGH-inertia tags (CC handles those)
  3. dispatch_started, then runner.build(): clone -> branch -> aider -> objective gate
  4. gate PASS -> close with a structured note; gate FAIL -> escalate to CC
     (the CC-validation path — Q4 automated-gate + CC-spot-check)

The build runs on a throwaway clone, commits to a work BRANCH (never main), and is
closed only when tests are green AND aider stayed in scope — a hollow build cannot
pass (proof-on-close in the builder itself).

Availability:
  ~/.granny/available/Aider.0.available.true  -> Granny will dispatch
  ~/.granny/available/Aider.0.available.false -> Granny skips Aider
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university._uu_root import uu_root
from unseen_university.identity import home_db_url
from unseen_university.capabilities import IdentityMixin

from .shim import AiderShim
from .consts import DEVICE_ID, INSTANCE_ABBREVIATION, DEFAULT_MODEL, MAX_INSTANCES

import json
import subprocess

log = logging.getLogger(__name__)

_START_TIME = time.time()
_CC_QUEUE = Path(uu_root()) / "devlab" / "claudecode" / "cc_queue.py"
_HIGH_INERTIA_TAGS = frozenset({"Security", "Provenance", "Database", "Auth", "Brainstem"})


class AiderDevice(IdentityMixin, BaseDevice):
    """Aider.0 — bus-dispatched aider builder. Dormant between dispatches; works one
    ticket synchronously on dispatch, then returns to listening."""

    DEVICE_ID = DEVICE_ID
    instance_abbreviation = INSTANCE_ABBREVIATION
    max_instances = MAX_INSTANCES

    def __init__(self, runner_fn=None) -> None:
        """runner_fn — injectable build callable (ticket_dict) -> AiderResult, for
        testability; the real path binds runner.build via _default_runner."""
        super().__init__()
        self._shim = AiderShim(device=self)
        self._runner_fn = runner_fn
        self._active_ticket: str | None = None
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []
        self._tickets_processed = 0
        self._tickets_escalated = 0
        # Repo the builder clones per ticket. Defaults to this checkout; overridable
        # so a swarm box can point at its own clone / a URL.
        self._repo_source = os.environ.get("AIDER_REPO_SOURCE", str(uu_root()))
        self._model = os.environ.get("AIDER_DEVICE_MODEL", DEFAULT_MODEL)

    # ── BaseDevice contract ────────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": self.instance_name,
            "version": "0.1.0",
            "purpose": "aider-backed autonomous ticket builder ($0 local inference)",
            "agent_class": "worker",
        }

    def health(self) -> dict:
        if self._blocked:
            return {"status": "unhealthy", "detail": f"blocked: {self._block_reason}", "checked_at": _now()}
        test = self._shim.self_test()
        if test.get("passed"):
            return {
                "status": "healthy",
                "detail": f"active={self._active_ticket or 'none'} built={self._tickets_processed} "
                          f"escalated={self._tickets_escalated} model={self._model}",
                "checked_at": _now(),
            }
        return {"status": "degraded", "detail": test.get("details", "unknown"), "checked_at": _now()}

    def startup_errors(self) -> list:
        return self._startup_errors

    def requirements(self) -> dict:
        return {
            "deps": ["aider (external venv, subprocess only)", "git"],
            "env": ["OLLAMA_API_BASE/HEX_OLLAMA", "AIDER_BIN (optional)", "AIDER_REPO_SOURCE (optional)"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["AIDER_WORKING", "AIDER_DONE", "AIDER_ESCALATE", "AIDER_ERROR"],
            "task_class": "worker",
        }

    def comms(self) -> dict:
        return {"address": f"comms://{self.DEVICE_ID}/inbox", "mode": "read_write"}

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def logs(self) -> dict:
        return {"paths": {"device": str(Path.home() / ".unseen_university" / "aider" / "device.log")}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {"host": os.environ.get("HOSTNAME", "localhost"), "pid": os.getpid()}

    def restart(self) -> None:
        self._shim.stop()
        self._active_ticket = None
        self._blocked = False
        self._block_reason = ""
        self._shim.start()

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason
        self._shim.stop()
        log.warning("AiderDevice: blocked — %s", reason)

    def halt(self) -> None:
        self.block("halt requested")

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self._active_ticket = None
        self._startup_errors = []

    # ── Queue interaction ──────────────────────────────────────────────────────

    def _run_queue_cmd(self, *args) -> dict | list | None:
        if not _CC_QUEUE.exists():
            log.warning("AiderDevice: cc_queue.py not found at %s", _CC_QUEUE)
            return None
        try:
            result = subprocess.run(
                ["python3", str(_CC_QUEUE)] + list(args),
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "UU_HOME_DB_URL": home_db_url()},
            )
            if result.returncode != 0:
                log.debug("AiderDevice: cc_queue %s failed: %s", args, result.stderr[:200])
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.debug("AiderDevice: cc_queue error: %s", exc)
            return None

    def _fetch_ticket(self, ticket_id: str) -> dict | None:
        res = self._run_queue_cmd("show", ticket_id)
        return res if isinstance(res, dict) else None

    # ── Channel / feed ─────────────────────────────────────────────────────────

    _EVENT_IMPORTANCE = {"working": 3, "done": 7, "escalated": 7, "error": 9}
    _PERSONAL_FEED = "aider/personal"

    def _channel_event(self, message: str, event_type: str = "working") -> None:
        importance = self._EVENT_IMPORTANCE.get(event_type, 3)
        try:
            from unseen_university.devices.bus.connection import make_bus_connection
            from unseen_university.devices.bus.envelope import Envelope
            if not hasattr(self, "_bus_conn"):
                self._bus_conn = make_bus_connection()
                self._bus_conn.create_mailbox(self._PERSONAL_FEED, feed_type="personal")
            env = Envelope.now(
                from_device=self.instance_name.lower(),
                to_device=self._PERSONAL_FEED,
                payload={"event": message, "kind": event_type},
            )
            env.importance = importance
            self._bus_conn.append(self._PERSONAL_FEED, env)
            log.info("AiderDevice: posted to %s importance=%d event=%s", self._PERSONAL_FEED, importance, event_type)
        except Exception as exc:
            log.warning("AiderDevice: bus post failed (%s), falling back to channel: %s", event_type, exc)
            try:
                from unseen_university.channel import post_to_channel
                post_to_channel(message, author="aider", channel="shared")
            except Exception as exc2:
                log.warning("AiderDevice: channel fallback also failed: %s", exc2)

    # ── Escalation ──────────────────────────────────────────────────────────────

    def _should_escalate(self, ticket: dict) -> tuple[bool, str]:
        """HIGH-inertia tickets go straight to CC (checked pre-build, saves a run)."""
        tags = set(ticket.get("tags", []))
        hit = tags & _HIGH_INERTIA_TAGS
        if hit:
            return True, f"HIGH-inertia tags: {sorted(hit)}"
        return False, ""

    def _escalate_ticket(self, ticket_id: str, reason: str, analysis: str = "") -> None:
        tried = (analysis[:500] + "...") if len(analysis) > 500 else analysis or "(no analysis captured)"
        summary_block = (
            "## Escalation summary (Aider)\n"
            f"**What was tried:** {tried}\n"
            f"**Where it broke:** {reason}\n"
            "**What now?**"
        )
        self._run_queue_cmd("append-note", ticket_id, summary_block)
        self._channel_event(f"AIDER_ESCALATE ticket={ticket_id} reason={reason!r}", event_type="escalated")
        self._run_queue_cmd("setstatus", ticket_id, "escalated")
        log.info("AiderDevice: escalated ticket %s to CC — %s", ticket_id, reason)
        self._tickets_escalated += 1
        self._active_ticket = None

    # ── Build ───────────────────────────────────────────────────────────────────

    def _default_runner(self, ticket: dict):
        """Bind runner.build with fields parsed from the ticket. Kept off the hot
        import path so importing the device never pulls runner's git/subprocess deps."""
        from .runner import build
        tid = ticket.get("id", "")
        message = self._compose_message(ticket)
        affected = self._parse_affected(ticket.get("description", ""))
        tests = self._parse_test_targets(ticket.get("description", ""))
        return build(
            tid, self._repo_source, message, model=self._model,
            add_files=affected or None, test_paths=tests or None,
            affected_files=affected or None,
        )

    def _run_build(self, ticket: dict):
        """Return an AiderResult (or raise). Uses the injected runner_fn if present."""
        fn = self._runner_fn or self._default_runner
        return fn(ticket)

    def _compose_message(self, ticket: dict) -> str:
        """Compose the aider task instruction from the ticket. Bounded so a huge
        description can't blow the context; firm no-edit-tests guardrail included."""
        title = ticket.get("title", "")
        desc = (ticket.get("description", "") or "")[:4000]
        return (
            f"Task: {title}\n\n{desc}\n\n"
            "Implement the change described above by editing the source files. "
            "Do NOT edit any test files, and do NOT modify files under .github/. "
            "Make the described tests pass, then stop."
        )

    @staticmethod
    def _parse_affected(desc: str) -> list[str]:
        """Extract repo-relative source paths from the '**Affected files:**' line."""
        m = re.search(r"\*\*Affected files:\*\*(.+?)(?:\n\*\*|\Z)", desc, re.S)
        if not m:
            return []
        paths = re.findall(r"[\w./-]+\.py", m.group(1))
        # Drop obvious test paths (never ask aider to touch them) + dedupe, keep order.
        seen, out = set(), []
        for p in paths:
            p = p.strip()
            if p in seen or "/test" in p or p.startswith("test_") or "tests/" in p:
                continue
            seen.add(p)
            out.append(p)
        return out

    @staticmethod
    def _parse_test_targets(desc: str) -> list[str]:
        """Extract test paths from the '**Test plan:**'/'**Test target:**' section."""
        m = re.search(r"\*\*Test (?:plan|target):\*\*(.+?)(?:\n\*\*|\Z)", desc, re.S)
        seg = m.group(1) if m else desc
        paths = re.findall(r"[\w./-]*tests?/[\w./-]+\.py(?:::[\w]+)?|[\w./-]*test_[\w./-]+\.py(?:::[\w]+)?", seg)
        seen, out = set(), []
        for p in paths:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    # ── Result ───────────────────────────────────────────────────────────────────

    def _post_result(self, ticket_id: str, result) -> str:
        """Close on a passed gate; escalate to CC otherwise. Returns the outcome
        string ('done' | 'escalated') for the listener's dispatch_done envelope."""
        if result is None:
            self._escalate_ticket(ticket_id, "runner returned no result")
            return "escalated"

        if not result.gate_passed:
            why = self._gate_failure_reason(result)
            self._escalate_ticket(ticket_id, why, analysis=result.aider_tail)
            return "escalated"

        note = (
            f"{self.instance_name}[{result.model}]: gate PASS — branch={result.branch} "
            f"files={result.changed_files} tests=green wall={result.wall_s:.1f}s "
            f"workdir={result.workdir}"
        )
        if result.scope_warnings:
            note += f" | scope-warn={result.scope_warnings}"
        close = self._run_queue_cmd("close", ticket_id, note[:2000])
        if close is None:
            show = self._run_queue_cmd("show", ticket_id)
            if show and show.get("status") in ("done", "closed"):
                log.info("AiderDevice: ticket %s already closed — success", ticket_id)
            else:
                self._escalate_ticket(ticket_id, "gate passed but close failed", analysis=note)
                return "escalated"
        self._tickets_processed += 1
        self._channel_event(f"AIDER_DONE ticket={ticket_id} branch={result.branch}", event_type="done")
        log.info("AiderDevice: closed ticket %s (branch %s)", ticket_id, result.branch)
        return "done"

    @staticmethod
    def _gate_failure_reason(result) -> str:
        if not result.edited:
            return "aider produced 0 edits"
        if result.scope_blocked:
            return f"diff-scope blocked: {result.scope_reasons}"
        if result.tests_green is None:
            return "no test target — correctness unverified (CC to validate)"
        if result.tests_green is False:
            return "tests red after aider edits"
        return "gate failed (unknown)"

    # ── Start/stop (delegate to shim) ─────────────────────────────────────────

    def start(self) -> bool:
        return self._shim.start()

    def stop(self) -> bool:
        return self._shim.stop()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
