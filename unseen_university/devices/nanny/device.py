"""
NannyOggDevice — scheduler + world-interaction dispatcher rack device.

Named for the Discworld witch who knows everyone and arranges everything.
Nanny asks "when and where?" — the complement to Granny Weatherwax's "what
and who?"

Responsibilities:
  - Internal scheduling: cron jobs, periodic tasks, consequence ticket
    maturation, alignment reviews, dreaming cadence.
  - External scheduling: world-interaction dispatch (calendar, IoT, sysadmin).
  - Agent registry: knows what agents exist and which ticket types they handle.
  - Gate monitoring: polls queue for consequence tickets whose gate date has passed.

Design: all routing is rule-based, zero inference. A schedule entry is a
{condition, action} pair. Conditions: cron, gate_date, ticket_closed, threshold.
Actions: dispatch_ticket, fire_consequence, route_world_ticket, post_to_channel.

D-granny-nanny-2026-05-28
# tags: Platform, Architecture
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from unseen_university.device import INTERFACE_VERSION, BaseDevice

_START_TIME = time.time()
_POLL_INTERVAL_S = float(os.environ.get("NANNY_POLL_INTERVAL_S", "60"))
_RUNTIME_ROOT = Path(
    os.environ.get("NANNY_RUNTIME_ROOT")
    or os.environ.get("IGOR_RUNTIME_ROOT")
    or Path.home() / ".unseen_university"
)
_LOG_FILE = _RUNTIME_ROOT / "logs" / "nanny_ogg.log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schedule entry ─────────────────────────────────────────────────────────────


@dataclass
class ScheduleEntry:
    """A single when→then rule managed by Nanny."""

    entry_id: str
    condition_type: str  # cron | gate_date | ticket_closed | threshold | external
    condition_params: dict[str, Any]
    action_type: str  # dispatch_ticket | fire_consequence | route_world | post_channel
    action_params: dict[str, Any]
    enabled: bool = True
    last_fired: str | None = None  # ISO timestamp
    fire_count: int = 0


# ── Agent registry entry ───────────────────────────────────────────────────────


@dataclass
class AgentRegistration:
    """A registered world-interaction agent and the ticket tags it handles."""

    agent_id: str
    handled_tags: list[str]
    dispatch_fn: Callable[[dict], bool] | None = None  # None until agent connects


# ── Built-in schedule defaults ─────────────────────────────────────────────────

_DEFAULT_SCHEDULE: list[dict[str, Any]] = [
    {
        "entry_id": "nightly_test_run",
        "condition_type": "cron",
        "condition_params": {"hour": 2, "minute": 0},  # 02:00 every night
        "action_type": "run_test_suite",
        "action_params": {"timeout": 300, "ignore": ["tests/e2e"]},
        "enabled": True,
    },
    {
        "entry_id": "weekly_audit_friday",
        "condition_type": "cron",
        "condition_params": {"weekday": 4, "hour": 18, "minute": 0},  # Friday 18:00
        "action_type": "post_channel",
        "action_params": {"channel": "shared", "message": "NANNY_TRIGGER:weekly_audit"},
        "enabled": True,
    },
    {
        "entry_id": "alignment_review_5_cycles",
        "condition_type": "threshold",
        "condition_params": {"metric": "cycles_without_human_contact", "threshold": 5},
        "action_type": "post_channel",
        "action_params": {
            "channel": "shared",
            "message": "NANNY_TRIGGER:alignment_review",
        },
        "enabled": True,
    },
    {
        "entry_id": "consequence_gate_monitor",
        "condition_type": "cron",
        "condition_params": {"interval_hours": 6},
        "action_type": "fire_consequence",
        "action_params": {},
        "enabled": True,
    },
    {
        "entry_id": "dreaming_daily",
        "condition_type": "cron",
        "condition_params": {"hour": 3, "minute": 0},  # 03:00 every night
        "action_type": "post_channel",
        "action_params": {
            "channel": "shared",
            "message": "NANNY_TRIGGER:dreaming_pass",
        },
        "enabled": True,
    },
    {
        "entry_id": "hourly_drift_detection",
        "condition_type": "cron",
        "condition_params": {"interval_hours": 1},
        "action_type": "run_auditor_baseline",
        "action_params": {"severity_min": "low"},
        "enabled": True,
    },
    {
        "entry_id": "nightly_code_sweep",
        "condition_type": "cron",
        "condition_params": {"hour": 4, "minute": 0},  # 04:00 every night
        "action_type": "run_code_sweep",
        "action_params": {},
        "enabled": True,
    },
    {
        "entry_id": "periodic_screenshot_capture",
        "condition_type": "cron",
        "condition_params": {"interval_hours": 1},  # every hour
        "action_type": "run_screenshot_capture",
        "action_params": {},
        "enabled": True,
    },
    {
        "entry_id": "nightly_annotator",
        "condition_type": "cron",
        "condition_params": {"hour": 4, "minute": 30},  # 04:30 daily, after code sweep
        "action_type": "run_annotator",
        "action_params": {"mode": "nightly"},
        "enabled": True,
    },
    {
        "entry_id": "nightly_repo_audit",
        "condition_type": "cron",
        "condition_params": {"hour": 3, "minute": 0},  # 03:00 daily
        "action_type": "run_repo_audit",
        "action_params": {},
        "enabled": True,
    },
    {
        "entry_id": "nightly_palace_updates",
        "condition_type": "cron",
        "condition_params": {"hour": 3, "minute": 15},  # 03:15 daily, after repo audit
        "action_type": "run_palace_updates",
        "action_params": {},
        "enabled": True,
    },
    {
        "entry_id": "nightly_context_prep",
        "condition_type": "cron",
        "condition_params": {"hour": 3, "minute": 30},  # 03:30 daily, after palace updates
        "action_type": "run_context_prep",
        "action_params": {},
        "enabled": True,
    },
]

# World-interaction ticket tags that Nanny routes to external agents
_WORLD_TAGS = frozenset(
    {
        "Calendar",
        "Email",
        "Social",
        "IoT",
        "SysAdmin",
        "WorldInteraction",
    }
)


class NannyOggDevice(BaseDevice):
    """Scheduler + world-interaction dispatcher rack device."""

    DEVICE_ID = "nanny-ogg"

    def __init__(self) -> None:
        super().__init__(device_id=self.DEVICE_ID)
        self._schedule: list[ScheduleEntry] = []
        self._agents: dict[str, AgentRegistration] = {}
        self._errors: list[str] = []
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._load_default_schedule()
        self._log = self._get_logger()

    # ── BaseDevice contract ────────────────────────────────────────────────────

    AGENT_CLASS = "specialized"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Nanny Ogg",
            "version": "0.1.0",
            "purpose": "Scheduler + world-interaction dispatcher. When/then engine for the rack.",
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {"deps": ["psycopg2"]}

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": [
                "NANNY_TRIGGER",
                "NANNY_DISPATCH",
                "NANNY_CONSEQUENCE",
            ],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._errors:
            return {
                "status": "degraded",
                "detail": self._errors[-1],
                "checked_at": _now_iso(),
            }
        thread_ok = self._poll_thread is not None and self._poll_thread.is_alive()
        return {
            "status": "healthy" if thread_ok else "degraded",
            "detail": f"{len(self._schedule)} schedule entries, {len(self._agents)} agents",
            "checked_at": _now_iso(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {"nanny": str(_LOG_FILE)}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def handle_unknown_prompt(self, prompt: str) -> str:
        """Return a Nanny Ogg quote for any prompt that isn't a scheduling command."""
        from unseen_university.devices.nanny.quotes import random_quote
        self._log.info("UNKNOWN_PROMPT prompt=%r — returning quote", prompt[:80])
        return random_quote()

    def where_and_how(self) -> dict:
        import os

        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m unseen_university.devices.nanny.device",
        }

    def restart(self) -> None:
        self.halt()
        self._stop_event.clear()
        self._start_poll_thread()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")
        self.halt()

    def halt(self) -> None:
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)

    def recovery(self) -> None:
        self._errors.clear()
        self.restart()

    def self_test(self) -> dict:
        return {
            "passed": True,
            "details": f"{len(self._schedule)} schedule entries loaded",
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the scheduling poll loop. Returns True on success."""
        try:
            self._start_poll_thread()
            return True
        except Exception as e:
            self._errors.append(f"start failed: {e}")
            return False

    def stop(self) -> bool:
        """Stop the scheduling poll loop. Returns True on success."""
        try:
            self.halt()
            return True
        except Exception as e:
            self._errors.append(f"stop failed: {e}")
            return False

    def rollback(self) -> None:
        self.halt()

    # ── Schedule management ────────────────────────────────────────────────────

    def add_entry(self, entry: ScheduleEntry) -> None:
        """Add or replace a schedule entry (keyed by entry_id)."""
        with self._lock:
            self._schedule = [e for e in self._schedule if e.entry_id != entry.entry_id]
            self._schedule.append(entry)

    def remove_entry(self, entry_id: str) -> bool:
        """Remove a schedule entry. Returns True if found and removed."""
        with self._lock:
            before = len(self._schedule)
            self._schedule = [e for e in self._schedule if e.entry_id != entry_id]
            return len(self._schedule) < before

    def list_entries(self) -> list[ScheduleEntry]:
        with self._lock:
            return list(self._schedule)

    # ── Agent registry ─────────────────────────────────────────────────────────

    def register_agent(
        self,
        agent_id: str,
        handled_tags: list[str],
        dispatch_fn: Callable[[dict], bool] | None = None,
    ) -> None:
        """Register an agent that handles tickets with the given tags.

        dispatch_fn(ticket_dict) → bool: returns True when dispatch succeeded.
        When None, Nanny posts to the agent's comms channel instead.
        """
        with self._lock:
            self._agents[agent_id] = AgentRegistration(
                agent_id=agent_id,
                handled_tags=handled_tags,
                dispatch_fn=dispatch_fn,
            )

    def route_world_ticket(self, ticket: dict) -> tuple[bool, str]:
        """Route a world-interaction ticket to the appropriate registered agent.

        Returns (dispatched: bool, agent_id: str). When no agent is registered
        for the ticket's tags, returns (False, "no_agent").
        """
        ticket_tags = set(ticket.get("tags", []))
        with self._lock:
            for agent_id, reg in self._agents.items():
                if ticket_tags & set(reg.handled_tags):
                    if reg.dispatch_fn is not None:
                        try:
                            ok = reg.dispatch_fn(ticket)
                            return (ok, agent_id)
                        except Exception as e:
                            self._errors.append(f"dispatch to {agent_id} failed: {e}")
                            return (False, agent_id)
                    else:
                        # Post to agent comms channel
                        self._post_to_channel(
                            agent_id,
                            f"NANNY_DISPATCH:{ticket.get('id', 'unknown')}",
                        )
                        return (True, agent_id)
        return (False, "no_agent")

    # ── Condition evaluation ────────────────────────────────────────────────────

    def check_entries(self, now: datetime | None = None) -> list[ScheduleEntry]:
        """Evaluate all schedule entries and return those whose condition is true.

        Does NOT fire actions — callers fire separately (testable this way).
        """
        if now is None:
            now = datetime.now(timezone.utc)
        triggered: list[ScheduleEntry] = []
        with self._lock:
            entries = list(self._schedule)

        for entry in entries:
            if not entry.enabled:
                continue
            if self._condition_met(entry, now):
                triggered.append(entry)

        return triggered

    def fire_entry(self, entry: ScheduleEntry) -> bool:
        """Fire the action for a schedule entry. Returns True on success."""
        try:
            action = entry.action_type
            params = entry.action_params

            if action == "post_channel":
                self._post_to_channel(
                    params.get("channel", "shared"),
                    params.get("message", ""),
                )
            elif action == "fire_consequence":
                self._check_consequence_gates()
            elif action == "route_world":
                ticket = params.get("ticket", {})
                self.route_world_ticket(ticket)
            elif action == "dispatch_ticket":
                self._dispatch_ticket(params.get("ticket_id", ""))
            elif action == "run_auditor_baseline":
                self._run_auditor_baseline(params)
            elif action == "run_test_suite":
                self._run_test_suite(params)
            elif action == "run_code_sweep":
                self._run_code_sweep(params)
            elif action == "run_screenshot_capture":
                self._run_screenshot_capture(params)
            elif action == "run_annotator":
                self._run_annotator(params)
            elif action == "run_repo_audit":
                self._run_repo_audit(params)
            elif action == "run_palace_updates":
                self._run_palace_updates(params)
            elif action == "run_context_prep":
                self._run_context_prep(params)

            entry.last_fired = _now_iso()
            entry.fire_count += 1
            return True

        except Exception as e:
            self._errors.append(f"fire_entry {entry.entry_id} failed: {e}")
            return False

    # ── Private helpers ────────────────────────────────────────────────────────

    def _get_logger(self):
        import logging

        log = logging.getLogger("nanny_ogg")
        if not log.handlers:
            log.setLevel(logging.INFO)
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(str(_LOG_FILE))
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        return log

    def _load_default_schedule(self) -> None:
        for raw in _DEFAULT_SCHEDULE:
            entry = ScheduleEntry(
                entry_id=raw["entry_id"],
                condition_type=raw["condition_type"],
                condition_params=raw["condition_params"],
                action_type=raw["action_type"],
                action_params=raw["action_params"],
                enabled=raw.get("enabled", True),
            )
            self._schedule.append(entry)

    def _start_poll_thread(self) -> None:
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="nanny-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                triggered = self.check_entries()
                for entry in triggered:
                    self._log.info("Firing: %s", entry.entry_id)
                    self.fire_entry(entry)
            except Exception as e:
                self._errors.append(f"poll_loop error: {e}")
                self._log.error("poll_loop error: %s", e)
            self._stop_event.wait(timeout=_POLL_INTERVAL_S)

    def _condition_met(self, entry: ScheduleEntry, now: datetime) -> bool:
        ctype = entry.condition_type
        params = entry.condition_params

        if ctype == "cron":
            # interval_hours: fire every N hours
            if "interval_hours" in params:
                interval_s = params["interval_hours"] * 3600
                if entry.last_fired is None:
                    return True
                last = datetime.fromisoformat(entry.last_fired)
                return (now - last).total_seconds() >= interval_s

            # weekday + hour + minute: fire on specific day/time (once per matching period)
            if "weekday" in params:
                if now.weekday() != params["weekday"]:
                    return False
                if now.hour != params.get("hour", 0):
                    return False
                if entry.last_fired is None:
                    return True
                last = datetime.fromisoformat(entry.last_fired)
                return (now - last).total_seconds() >= 86400  # once per day

            # hour + minute: fire daily at a specific time
            if "hour" in params and "minute" in params:
                if now.hour != params["hour"]:
                    return False
                if abs(now.minute - params["minute"]) > 2:
                    return False
                if entry.last_fired is None:
                    return True
                last = datetime.fromisoformat(entry.last_fired)
                return (now - last).total_seconds() >= 86400

        elif ctype == "gate_date":
            gate = params.get("gate_date", "")
            if not gate:
                return False
            try:
                gate_dt = datetime.fromisoformat(gate).replace(tzinfo=timezone.utc)
                return now >= gate_dt
            except ValueError:
                return False

        elif ctype == "threshold":
            # threshold conditions are evaluated externally — always False from Nanny's side
            # External systems set a counter; Nanny fires when check_threshold() is called
            return False

        return False

    def _check_consequence_gates(self) -> None:
        """Surface consequence tickets whose gate date has passed to the queue."""
        try:
            import os
            import sys

            queue_py = Path(os.environ.get("CC_WORKFLOW_TOOLS", "")) / "cc_queue.py"
            if not queue_py.exists():
                return

            import importlib.util

            spec = importlib.util.spec_from_file_location("cc_queue", queue_py)
            cc_queue = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cc_queue)

            now_str = datetime.now(timezone.utc).date().isoformat()
            # Post a trigger to the shared channel — queue polling handles the rest
            self._post_to_channel(
                "shared",
                f"NANNY_CONSEQUENCE_GATE_CHECK:{now_str}",
            )
        except Exception as e:
            self._log.warning("consequence gate check failed: %s", e)

    def _cc_session_active(self) -> bool:
        """Return True if a CC session appears active (skip test run during human work)."""
        flag_dir = Path.home() / ".granny" / "available"
        return (flag_dir / "CC.0.available.true").exists()

    def _run_test_suite(self, params: dict) -> None:
        """Run pytest suite if no CC session is active, post NIGHTLY_TEST_RESULT to channel."""
        import re
        import subprocess
        import sys
        import time

        if self._cc_session_active():
            self._log.info("_run_test_suite: CC session active — skipping")
            self._post_to_channel("shared", "NIGHTLY_TEST_SKIPPED|reason=cc_session_active")
            return

        timeout = int(params.get("timeout", 300))
        ignore_paths = params.get("ignore", ["tests/e2e"])
        repo_root = Path(__file__).parent.parent.parent

        cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no",
               f"--timeout={timeout}"]
        for ign in ignore_paths:
            cmd += ["--ignore", ign]

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 60,
                cwd=str(repo_root),
            )
            duration = int(time.time() - t0)
            output = result.stdout + result.stderr
            passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", output)) else 0
            failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", output)) else 0
            error = int(m.group(1)) if (m := re.search(r"(\d+) error", output)) else 0
            self._post_to_channel(
                "shared",
                f"NIGHTLY_TEST_RESULT|passed={passed}|failed={failed}|error={error}|duration={duration}s",
            )
            self._log.info(
                "test suite: passed=%d failed=%d error=%d in %ds", passed, failed, error, duration
            )
        except subprocess.TimeoutExpired:
            self._post_to_channel(
                "shared", f"NIGHTLY_TEST_RESULT|status=timeout|timeout={timeout}s"
            )
            self._log.warning("test suite timed out after %ds", timeout)
        except Exception as exc:
            self._log.error("_run_test_suite failed: %s", exc)

    def _post_to_channel(self, channel: str, message: str) -> None:
        """Post a message to the specified channel."""
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(message, author="nanny-ogg", channel=channel)
            self._log.info("channel post → %s: %s", channel, message)
        except Exception as e:
            self._log.warning("channel post failed (%s): %s", channel, e)

    def _dispatch_ticket(self, ticket_id: str) -> None:
        """Dispatch a specific ticket to its executor."""
        self._post_to_channel("shared", f"NANNY_DISPATCH_TICKET:{ticket_id}")

    def _run_auditor_baseline(self, params: dict) -> None:
        """Run all baseline drift checks and file tickets for FAIL findings.

        Interim bridge until T-learning-loop-generalized ships — nanny owns the
        finding→ticket step because it can_send and the auditor is read_only.
        """
        try:
            from unseen_university.devices.auditor.device import AuditorDevice

            auditor = AuditorDevice()
            severity_min = params.get("severity_min", "low")
            findings = auditor.run_all(kind="baseline", severity_min=severity_min)
            for finding in findings:
                if finding.get("status") == "FAIL":
                    self._file_drift_ticket(finding)
        except Exception as e:
            self._errors.append(f"_run_auditor_baseline failed: {e}")
            self._log.error("_run_auditor_baseline failed: %s", e)

    def _run_code_sweep(self, params: dict) -> None:
        """Run the AST code sweep and post results to channel."""
        try:
            import os
            from unseen_university.devices.nanny.sweeps.code_sweep import run_sweep

            db_url = home_db_url()
            result = run_sweep(db_url=db_url)
            msg = (
                f"CODE_SWEEP_RESULT|inserted={result['inserted']}|"
                f"updated={result['updated']}|unchanged={result['unchanged']}|"
                f"errors={result['errors']}"
            )
            self._post_to_channel("shared", msg)
            self._log.info(
                "code sweep: inserted=%d updated=%d unchanged=%d errors=%d",
                result["inserted"], result["updated"], result["unchanged"], result["errors"],
            )
        except Exception as e:
            self._errors.append(f"_run_code_sweep failed: {e}")
            self._log.error("_run_code_sweep failed: %s", e)

    def _run_annotator(self, params: dict) -> None:
        """Run the codebase annotator and post results to channel."""
        try:
            import os
            from unseen_university.devices.classifier.annotator import run_annotator

            db_url = home_db_url()
            mode = params.get("mode", "nightly")
            result = run_annotator(db_url=db_url, mode=mode)
            msg = (
                f"ANNOTATOR_RESULT|mode={mode}|modules={result['modules']}|"
                f"inserted={result['inserted']}|updated={result['updated']}|"
                f"errors={result['errors']}"
            )
            self._post_to_channel("shared", msg)
            self._log.info(
                "annotator: mode=%s modules=%d inserted=%d updated=%d errors=%d",
                mode, result["modules"], result["inserted"], result["updated"], result["errors"],
            )
        except Exception as e:
            self._errors.append(f"_run_annotator failed: {e}")
            self._log.error("_run_annotator failed: %s", e)

    def _run_repo_audit(self, params: dict) -> None:
        """Run structural repo audit and post HIGH-severity flags to channel."""
        try:
            import os
            from unseen_university.devices.hubert.repo_auditor import run_structural_audit

            repo_path = params.get("repo_path", ".")
            flags = run_structural_audit(repo_path=repo_path)
            high_flags = [f for f in flags if f.severity == "HIGH"]
            self._post_to_channel(
                "shared",
                f"REPO_AUDIT_RESULT|flags={len(flags)}|high={len(high_flags)}",
            )
            for flag in high_flags:
                self._post_to_channel(
                    "shared",
                    f"REPO_AUDIT_HIGH|ticket={flag.ticket_id}|signal={flag.signal}|"
                    f"detail={flag.detail[:120]}",
                )
            self._log.info(
                "repo audit: flags=%d high=%d", len(flags), len(high_flags)
            )
        except Exception as e:
            self._errors.append(f"_run_repo_audit failed: {e}")
            self._log.error("_run_repo_audit failed: %s", e)

    def _run_palace_updates(self, params: dict) -> None:
        """Run nightly palace update: decision nodes + session brief."""
        try:
            from datetime import timezone as _tz
            from devlab.claudecode.cc_nightly_palace_updates import run as _palace_run

            date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
            summary = _palace_run(date=date, dry_run=False, all_docs=False)
            self._post_to_channel(
                "shared",
                f"PALACE_UPDATE_RESULT|date={date}|decisions={summary['decisions_written']}|"
                f"brief={'ok' if summary['session_brief_written'] else 'failed'}",
            )
            self._log.info(
                "palace update: decisions=%d brief=%s",
                summary["decisions_written"],
                summary["session_brief_written"],
            )
        except Exception as e:
            self._errors.append(f"_run_palace_updates failed: {e}")
            self._log.error("_run_palace_updates failed: %s", e)

    def _run_context_prep(self, params: dict) -> None:
        """Run nightly context prep: write tomorrow's palace.sessions.DATE+1.brief."""
        try:
            from datetime import timezone as _tz
            from devlab.claudecode.cc_nightly_context_prep import run as _ctx_run

            date = datetime.now(_tz.utc).strftime("%Y-%m-%d")
            summary = _ctx_run(date=date, dry_run=False)
            ok = summary.get("context_brief_written", False)
            self._post_to_channel(
                "shared",
                f"CONTEXT_PREP_RESULT|date={date}|ok={'yes' if ok else 'no'}",
            )
            self._log.info("context prep: date=%s ok=%s", date, ok)
        except Exception as e:
            self._errors.append(f"_run_context_prep failed: {e}")
            self._log.error("_run_context_prep failed: %s", e)

    def _run_screenshot_capture(self, params: dict) -> None:
        """Capture fascia screenshots for all online devices."""
        try:
            from unseen_university.devices.web_server.screenshot_capture import capture_all

            results = capture_all()
            ok_count = sum(1 for v in results.values() if v)
            total = len(results)
            msg = f"SCREENSHOT_CAPTURE_RESULT|ok={ok_count}|total={total}"
            self._post_to_channel("shared", msg)
            self._log.info("screenshot capture: ok=%d total=%d", ok_count, total)
        except Exception as e:
            self._log.warning("_run_screenshot_capture failed: %s", e)

    def _file_drift_ticket(self, finding: dict) -> None:
        """File a cc_queue ticket for a drift FAIL. Date-scoped ID provides daily dedup."""
        import json as _json
        import subprocess
        import sys

        try:
            queue_py = Path(os.environ.get("CC_WORKFLOW_TOOLS", "")) / "cc_queue.py"
            if not queue_py.exists():
                self._log.warning(
                    "cc_queue.py not found — cannot file drift ticket for %s",
                    finding.get("name", "?"),
                )
                return
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            check_name = finding.get("name", "unknown")
            ticket_id = f"T-drift-{check_name}-{today}"
            detail = finding.get("detail", "")
            description = (
                f"Baseline check FAIL — {detail}\n\n"
                f"**Affected files:** The component emitting the metric tracked by "
                f"`{check_name}`. Investigate call logs or memory writes to find the spike source.\n\n"
                f"**Scope boundary:** IN — diagnose rate spike, confirm or adjust baseline "
                f"threshold; OUT — changes to drift check definitions (file separately).\n\n"
                f"**Completion criteria:** Rate returns to within normal baseline range and "
                f"finding shows PASS on next hourly scan; or root cause documented and "
                f"threshold adjusted."
            )
            ticket = {
                "id": ticket_id,
                "title": f"Drift spike: {check_name}",
                "description": description,
                "size": "S",
                "worker": "claude",
                "tags": ["Drift", "Platform"],
                "target_difficulty": 1,
                "status": "triage",
            }
            result = subprocess.run(
                [sys.executable, str(queue_py), "add", _json.dumps(ticket)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self._log.info(
                "Drift ticket filing for %s: %s", check_name, result.stdout.strip()
            )
        except Exception as e:
            self._log.warning(
                "_file_drift_ticket failed for %s: %s", finding.get("name", "?"), e
            )
