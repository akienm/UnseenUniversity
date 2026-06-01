"""
GrannyWeatherwaxDevice — ticket gateway + coding orchestrator rack device.

Named for the Discworld witch who knows what needs doing and makes sure
it gets done. Granny asks "what and who?" — the complement to Nanny Ogg's
"when and where?"

Responsibilities:
  - Ticket intake: deterministic filing-time audit gate.
  - Routing: tag → worker via capability graph (graph traversal, no inference).
  - Hebbian strengthening: successful routes increase edge weight.
  - CC escalation: novel tags or HIGH-inertia tickets surface to CC.
  - Status tracking: single source of truth for work state.

Design: cc_queue.py is storage underneath. Granny is the decision layer.
Routing is graph traversal in the common case. Novel routing escalates to
Haiku/Sonnet; result compiles back as a new edge.

D-granny-nanny-2026-05-28
# tags: Platform, Architecture
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from unseen_university.device import INTERFACE_VERSION, BaseDevice

_START_TIME = time.time()
_RUNTIME_ROOT = Path(
    os.environ.get("GRANNY_RUNTIME_ROOT")
    or os.environ.get("IGOR_RUNTIME_ROOT")
    or Path.home() / ".unseen_university"
)
_LOG_FILE = _RUNTIME_ROOT / "logs" / "granny_weatherwax.log"

# Tags that escalate to CC — cross-device platform concerns only.
# Granny routes at the device level; each device owns its internal inertia.
# "Architecture" is intentionally absent: Igor's brainstem/cognition safety
# is Igor's concern, not Granny's. Only tickets that change how the RACK
# itself works (contracts between devices, rack topology, cross-device APIs)
# belong here.
_CC_ESCALATION_TAGS = frozenset(
    {"RackContract", "CrossDevice", "DeviceInterface", "Security", "DataMigration"}
)

# Default routing: maps ticket tags → worker IDs (deterministic common cases).
_DEFAULT_ROUTING: dict[str, list[str]] = {
    "Cognition": ["igor"],
    "Memory": ["igor", "librarian"],
    "Database": ["igor"],
    "Scraps": ["scraps"],
    "Platform": ["cc"],
    "Infrastructure": ["cc"],
    "tests": ["cc"],
    "Training": ["igor"],
    "Research": ["librarian"],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class RoutingEdge:
    """A weighted edge in the capability graph: tag → worker."""

    tag: str
    worker_id: str
    weight: float = 1.0  # Hebbian: increases on successful dispatch
    dispatch_fn: Callable[[dict], bool] | None = None
    fire_count: int = 0
    last_fired: str | None = None


@dataclass
class WorkerNode:
    """A registered worker that can receive tickets."""

    worker_id: str
    handled_tags: list[str]
    dispatch_fn: Callable[[dict], bool] | None = None


@dataclass
class AuditResult:
    """Result of the filing-time audit gate."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    escalate_to_cc: bool = False


# ── Audit checks ───────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = ("id", "title", "description", "size")
_VALID_SIZES = {"S", "M", "L", "XL"}


def _audit_ticket(ticket: dict[str, Any]) -> AuditResult:
    """Deterministic filing-time audit gate.

    Checks required fields, valid size, description sections.
    Returns AuditResult(passed, reasons, escalate_to_cc).
    """
    reasons: list[str] = []

    for f in _REQUIRED_FIELDS:
        if not ticket.get(f):
            reasons.append(f"missing field: {f}")

    size = ticket.get("size", "")
    if size not in _VALID_SIZES:
        reasons.append(f"invalid size: {size!r} (must be S/M/L/XL)")

    desc = ticket.get("description", "")
    for section in ("Affected files", "Scope boundary", "Completion criteria"):
        if section.lower() not in desc.lower():
            reasons.append(f"description missing section: {section}")

    tags = set(ticket.get("tags", []))
    escalate = bool(tags & _CC_ESCALATION_TAGS)
    if escalate:
        reasons.append(
            f"platform-level tags require CC approval: {tags & _CC_ESCALATION_TAGS}"
        )

    return AuditResult(
        passed=not reasons or escalate, reasons=reasons, escalate_to_cc=escalate
    )


# ── Device ─────────────────────────────────────────────────────────────────────


class GrannyWeatherwaxDevice(BaseDevice):
    """Ticket gateway + coding orchestrator rack device."""

    DEVICE_ID = "granny-weatherwax"

    def __init__(self) -> None:
        super().__init__(device_id=self.DEVICE_ID)
        self._edges: dict[str, list[RoutingEdge]] = {}  # tag → edges
        self._workers: dict[str, WorkerNode] = {}
        self._ticket_status: dict[str, str] = {}  # ticket_id → status
        self._cc_pids: dict[str, int] = {}  # ticket_id → spawned CC worker pid
        self._errors: list[str] = []
        self._lock = threading.Lock()
        self._log = self._get_logger()
        self._load_default_routing()

    # ── BaseDevice contract ────────────────────────────────────────────────────

    AGENT_CLASS = "specialized"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Granny Weatherwax",
            "version": "0.1.0",
            "purpose": "Ticket gateway + coding orchestrator. What/who engine for the rack.",
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {"deps": ["psycopg2"]}

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": [
                "GRANNY_ROUTE",
                "GRANNY_ESCALATE",
                "GRANNY_AUDIT_FAIL",
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
        return {
            "status": "healthy",
            "detail": (
                f"{sum(len(v) for v in self._edges.values())} routing edges, "
                f"{len(self._workers)} workers, "
                f"{len(self._ticket_status)} tickets tracked"
            ),
            "checked_at": _now_iso(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {"granny": str(_LOG_FILE)}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m devices.granny.device",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()

    def self_test(self) -> dict:
        edge_count = sum(len(v) for v in self._edges.values())
        return {
            "passed": True,
            "details": f"{edge_count} routing edges, {len(self._workers)} workers",
        }

    # ── Worker registry ────────────────────────────────────────────────────────

    def register_worker(
        self,
        worker_id: str,
        handled_tags: list[str],
        dispatch_fn: Callable[[dict], bool] | None = None,
    ) -> None:
        """Register a worker that handles tickets with the given tags."""
        with self._lock:
            self._workers[worker_id] = WorkerNode(
                worker_id=worker_id,
                handled_tags=handled_tags,
                dispatch_fn=dispatch_fn,
            )
            for tag in handled_tags:
                edge = RoutingEdge(
                    tag=tag, worker_id=worker_id, dispatch_fn=dispatch_fn
                )
                self._edges.setdefault(tag, []).append(edge)
        self._log.info(
            "registered worker %s tags=%s has_dispatch_fn=%s",
            worker_id,
            handled_tags,
            dispatch_fn is not None,
        )

    # ── Ticket intake ──────────────────────────────────────────────────────────

    def intake_ticket(self, ticket: dict[str, Any]) -> AuditResult:
        """Audit gate — validate ticket at filing time.

        Returns AuditResult. When escalate_to_cc=True, call escalate_to_cc()
        before routing — HIGH-inertia tickets need human approval.
        """
        result = _audit_ticket(ticket)
        tid = ticket.get("id", "?")
        if not result.passed:
            self._log.warning("audit FAIL %s: %s", tid, result.reasons)
            self.trace_record(
                "granny_audit_fail", {"ticket_id": tid, "reasons": result.reasons}
            )
            reasons_str = "; ".join(result.reasons)
            self._post_to_channel(
                "shared",
                f"Granny: ticket {tid} failed audit — {reasons_str}",
            )
        elif result.escalate_to_cc:
            self._log.info("audit ESCALATE %s: HIGH-inertia", tid)
            self._post_to_channel(
                "shared",
                f"Granny: {tid} has HIGH-inertia tags — escalating to CC for approval",
            )
        return result

    # ── Routing ────────────────────────────────────────────────────────────────

    def route_ticket(self, ticket: dict[str, Any]) -> tuple[bool, str]:
        """Route a ticket to the best-weighted worker for its tags.

        Returns (dispatched: bool, worker_id: str).
        When no edge exists for any tag: returns (False, "no_route") and
        posts GRANNY_ESCALATE so CC can define a new route.
        """
        ticket_tags = set(ticket.get("tags", []))

        # Always escalate HIGH-inertia tickets to CC
        if ticket_tags & _CC_ESCALATION_TAGS:
            self.escalate_to_cc(ticket, "HIGH-inertia tags require CC approval")
            return (False, "escalated_to_cc")

        # Find best edge: highest weight; break ties by preferring edges with
        # a dispatch_fn over skeleton edges (weight-only, no fn).
        best_edge: RoutingEdge | None = None
        with self._lock:
            for tag in ticket_tags:
                for edge in self._edges.get(tag, []):
                    if best_edge is None:
                        best_edge = edge
                    elif edge.weight > best_edge.weight:
                        best_edge = edge
                    elif (
                        edge.weight == best_edge.weight
                        and edge.dispatch_fn is not None
                        and best_edge.dispatch_fn is None
                    ):
                        best_edge = edge

        if best_edge is None:
            self.escalate_to_cc(ticket, f"no routing edge for tags {ticket_tags}")
            return (False, "no_route")

        # Dispatch
        try:
            if best_edge.dispatch_fn is not None:
                ok = best_edge.dispatch_fn(ticket)
            else:
                tid = ticket.get("id", "?")
                title = ticket.get("title", "")[:60]
                size = ticket.get("size", "S")
                tags = ",".join(ticket.get("tags", []))
                self._post_to_channel(
                    "shared",
                    f"GRANNY_DISPATCH|ticket={tid}|worker={best_edge.worker_id}|size={size}|tags={tags}|title={title}",
                )
                ok = True

            if ok:
                self.strengthen_edge(best_edge.tag, best_edge.worker_id)
                self.track_status(ticket.get("id", "?"), "routed")
                self._log.info(
                    "routed %s → %s (weight=%.2f)",
                    ticket.get("id"),
                    best_edge.worker_id,
                    best_edge.weight,
                )
                self.trace_record(
                    "granny_route",
                    {
                        "ticket_id": ticket.get("id"),
                        "worker_id": best_edge.worker_id,
                        "tag": best_edge.tag,
                        "weight": best_edge.weight,
                    },
                )
            return (ok, best_edge.worker_id)

        except Exception as e:
            self._errors.append(f"route {ticket.get('id')}: {e}")
            return (False, best_edge.worker_id)

    # ── Hebbian strengthening ──────────────────────────────────────────────────

    def strengthen_edge(self, tag: str, worker_id: str, delta: float = 0.1) -> None:
        """Increase weight of a routing edge after successful dispatch.

        Weight is capped at 10.0 to prevent runaway strengthening.
        """
        with self._lock:
            for edge in self._edges.get(tag, []):
                if edge.worker_id == worker_id:
                    edge.weight = min(edge.weight + delta, 10.0)
                    edge.fire_count += 1
                    edge.last_fired = _now_iso()
                    self._log.debug(
                        "strengthen %s→%s weight=%.2f", tag, worker_id, edge.weight
                    )
                    return

    def weaken_edge(self, tag: str, worker_id: str, delta: float = 0.2) -> None:
        """Decrease weight of a routing edge after failed dispatch.

        Weight floored at 0.1 (never fully forgotten).
        """
        with self._lock:
            for edge in self._edges.get(tag, []):
                if edge.worker_id == worker_id:
                    edge.weight = max(edge.weight - delta, 0.1)
                    self._log.info(
                        "weaken %s→%s weight=%.2f (delta=%.2f)",
                        tag,
                        worker_id,
                        edge.weight,
                        delta,
                    )
                    return

    def get_edge_weights(self, tag: str) -> list[tuple[str, float]]:
        """Return [(worker_id, weight)] for all edges on this tag, sorted by weight."""
        with self._lock:
            edges = self._edges.get(tag, [])
            return sorted(((e.worker_id, e.weight) for e in edges), key=lambda x: -x[1])

    # ── Escalation ────────────────────────────────────────────────────────────

    def escalate_to_cc(self, ticket: dict[str, Any], reason: str) -> None:
        """Post escalation to shared channel so CC can handle this ticket."""
        tid = ticket.get("id", "?")
        title = ticket.get("title", "")[:60]
        self._post_to_channel(
            "shared",
            f"Granny: needs CC — {tid} ({title}): {reason}",
        )
        self._log.info("escalated %s: %s", tid, reason)
        self.trace_record("granny_escalate", {"ticket_id": tid, "reason": reason})

    # ── Status tracking ────────────────────────────────────────────────────────

    def track_status(self, ticket_id: str, status: str) -> None:
        with self._lock:
            self._ticket_status[ticket_id] = status

    def get_status(self, ticket_id: str) -> str | None:
        with self._lock:
            return self._ticket_status.get(ticket_id)

    def list_statuses(self) -> dict[str, str]:
        with self._lock:
            return dict(self._ticket_status)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _get_logger(self):
        import logging

        log = logging.getLogger("granny_weatherwax")
        if not log.handlers:
            log.setLevel(logging.INFO)
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(str(_LOG_FILE))
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        return log

    def _dispatch_to_cc(self, ticket: dict) -> bool:
        """Dispatch a ticket to CC: post GRANNY_DISPATCH + spawn claude worker."""
        tid = ticket.get("id", "?")
        title = ticket.get("title", "")[:60]
        size = ticket.get("size", "S")
        tags = ",".join(ticket.get("tags", []))

        self._post_to_channel(
            "shared",
            f"GRANNY_DISPATCH|ticket={tid}|worker=cc|size={size}|tags={tags}|title={title}",
        )

        # Dedup: if a CC process for this ticket is still alive, skip
        existing_pid = self._cc_pids.get(tid)
        if existing_pid:
            try:
                os.kill(existing_pid, 0)  # signal 0 = existence check
                self._log.info(
                    "CC worker already running for %s (pid=%d)", tid, existing_pid
                )
                return True
            except OSError:
                del self._cc_pids[tid]

        claude_bin = shutil.which("claude")
        if not claude_bin:
            self._log.error(
                "claude binary not found — cannot spawn CC worker for %s", tid
            )
            return False

        try:
            proc = subprocess.Popen(
                [claude_bin, "--dangerously-skip-permissions", f"/sprint-ticket {tid}"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._cc_pids[tid] = proc.pid
            self._log.info("spawned CC worker for %s (pid=%d)", tid, proc.pid)
            self.trace_record("granny_dispatch_cc", {"ticket_id": tid, "pid": proc.pid})
            return True
        except Exception as e:
            self._log.error("failed to spawn CC worker for %s: %s", tid, e)
            return False

    def _load_default_routing(self) -> None:
        for tag, worker_ids in _DEFAULT_ROUTING.items():
            for worker_id in worker_ids:
                dispatch_fn = self._dispatch_to_cc if worker_id == "cc" else None
                edge = RoutingEdge(
                    tag=tag, worker_id=worker_id, dispatch_fn=dispatch_fn
                )
                self._edges.setdefault(tag, []).append(edge)

    def _post_to_channel(self, channel: str, message: str) -> None:
        self._log.info("channel → %s: %.120s", channel, message)
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(message, author="granny-weatherwax", channel=channel)
        except Exception as e:
            self._log.warning("channel post failed (%s): %s", channel, e)
