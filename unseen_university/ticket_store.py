"""Filesystem ticket-store — the single queue chokepoint (no Postgres).

Decision D-build-queue-filesystem-first-2026-06-19. This is the queue analogue of
``memory_emit.py``: ONE module that owns the filesystem ticket store so the ~21
ticket-state readers/writers migrate by swapping their SQL for one import, rather
than scattering JSON-parsing reimplementations (fix-one-leave-many).

Storage layout (under ``$UU_MEMORY_ROOT`` or ``<repo>/devlab/runtime/memory``):
    tickets/*.json          — in-flight tickets (the dynamic queue)
    tickets/closed/*.json    — terminal tickets (closed/done/cancelled)

Design rules honoured:
- **NO SQLITE / NO POSTGRES.** This module imports no DB driver and never reads
  ``clan.memories`` / ``devlab.tickets``. Pure filesystem.
- **Atomic write+rename (PATTERN-004).** Every write goes to a ``.tmp`` then
  ``os.replace`` — readers never see a torn file. ``close`` MOVES the file with a
  single atomic ``os.replace`` (rewrite-in-place, then rename into ``closed/``), so
  a ticket is NEVER present in both dirs; the worst crash residue is a
  closed-status file still sitting in ``tickets/`` — benign, because callers trust
  ``body.status``, not directory membership.
- **Concurrency.** A coarse advisory ``fcntl.flock`` serialises all *mutations*
  across processes (Granny's ``set_worker`` vs CC's ``close``); reads are lock-free
  because atomic files are always valid. The lock's job is lost-update prevention
  (atomic-replace alone does not stop two updaters clobbering each other's fields).
- **Log every state change** (status/worker transitions, the close-move).

Envelope schema matches ``memory_emit.py`` byte-for-byte
(``id/emitter/namespace/category/kind/emitted_at/links/body``) so the
completeness-audit and any projector keep reading these files unchanged. We
re-implement the ~10 lines rather than import ``devlab.claudecode.memory_emit``:
the queue chokepoint must be importable by the packaged devices (Granny,
Librarian, web server) and must not couple to the memory-store chokepoint.

DEFERRED (out of scope for this module):
- Constraint-stamping (cc_queue's add path writes ``devlab.constraints`` in
  Postgres) — a DB concern; re-home when constraints move off Postgres.
- Gate-aware ``next_for_worker`` — gate evaluation stays the caller's job for now
  (see T-ticket-readers-migrate / Granny's workflow_executor).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import fcntl

from unseen_university.memory_root import memory_root as _memory_root
from unseen_university.gate_logic import TERMINAL_STATUSES

log = logging.getLogger(__name__)

# Stored statuses a worker may be dispatched onto. Display taxonomy (READY etc.)
# is derived elsewhere; these are the literal stored strings.
WORKABLE_STATUSES = {"sprint", "assigned"}

# Default envelope link buckets (mirrors memory_emit).
_LINK_KEYS = ("decisions", "tickets", "commits", "whys")


# ── Paths (env read dynamically so tests can point UU_MEMORY_ROOT at a tmp dir) ──
# _memory_root imported above from the shared resolver (unseen_university.memory_root).


def _tickets_dir() -> Path:
    return _memory_root() / "tickets"


def _closed_dir() -> Path:
    return _tickets_dir() / "closed"


def _lock_path() -> Path:
    return _tickets_dir() / ".queue.lock"


# ── Primitives ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Forensic device id for the build queue. The queue's eventual home is a rack
# device (devices/queue/device.py) that inherits DiagnosticBase; until then this
# module IS the queue chokepoint, so it owns the forensic log here. The record
# schema ({ts, device, event, data}) and the logs/<device>/trace/
# location are IDENTICAL to DiagnosticBase.trace_record, so when the queue
# becomes a device the call swaps to self.trace_record with zero schema change
# and existing readers (last_traces) keep working.
_FORENSIC_DEVICE = "queue"


def _forensic(event: str, data: Optional[dict] = None) -> None:
    """Append one forensic trace record for a queue state-change / interface
    crossing (CLAUDE.md: log every state change and interface crossing; AR-009).

    Single forensic owner for the queue: every caller (cc_queue, Granny, the
    future rack device) that mutates through this chokepoint gets a device_id-
    stamped JSONL trace for free — the queue analogue of memory_emit owning the
    write. Never raises (forensics must not break a queue mutation).
    """
    try:
        env = os.environ.get("UU_QUEUE_TRACE_DIR")
        trace_dir = Path(env) if env else (
            Path.home() / ".unseen_university" / "logs"
            / _FORENSIC_DEVICE / "trace"
        )
        trace_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        record = {"ts": ts.isoformat(), "device": _FORENSIC_DEVICE, "event": event}
        if data is not None:
            record["data"] = data
        day_file = trace_dir / f"{ts.strftime('%Y%m%d')}.jsonl"
        with day_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def _atomic_write(path: Path, record: dict) -> None:
    """Write JSON to a temp sibling then atomically replace — never a torn file."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


@contextmanager
def _mutation_lock():
    """Coarse cross-process exclusive lock around every mutation.

    A fresh fd per acquisition so the flock is honoured both across processes and
    across same-process callers (flock is scoped to the open file description).
    """
    _tickets_dir().mkdir(parents=True, exist_ok=True)
    f = open(_lock_path(), "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def _envelope(body: dict) -> dict:
    """Wrap a ticket body in the canonical memory_emit envelope (new tickets)."""
    emitter = body.get("created_by") or "cc.0"
    tid = body["id"]
    ns = [tid]
    now = datetime.now()
    stamp = now.strftime("%Y%m%d.%H%M%S%f")
    stem = f"{emitter}." + ".".join(ns) + f".{stamp}"
    links = {k: [] for k in _LINK_KEYS}
    links["tickets"] = [tid]
    if body.get("decision_id"):
        links["decisions"] = [body["decision_id"]]
    # produced_by (feedback-edges contract): a ticket's backward edge is the
    # decision that produced it; absent a decision, the honest session fallback.
    # Additive — no reader may require it; legacy envelopes lack it.
    produced_by = body.get("decision_id") or f"session:{emitter}"
    return {
        "id": stem,
        "emitter": emitter,
        "namespace": ns,
        "category": "tickets",
        "kind": "ticket",
        "emitted_at": now.astimezone(timezone.utc).isoformat(),
        "links": links,
        "produced_by": produced_by,
        "body": body,
    }


def _iter_files(include_closed: bool = False) -> Iterator[tuple[Path, dict]]:
    dirs = [_tickets_dir()]
    if include_closed:
        dirs.append(_closed_dir())
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:  # unreadable file — skip, never crash a read
                log.warning("ticket_store: unreadable file %s: %s", p.name, exc)
                continue
            if isinstance(rec, dict) and isinstance(rec.get("body"), dict):
                yield p, rec


def _find(ticket_id: str) -> tuple[Optional[Path], Optional[dict]]:
    """Locate a ticket by logical body.id. Active first, then closed/.

    Atomic-rename close guarantees a ticket is never in both dirs, so active-first
    is unambiguous.
    """
    for p, rec in _iter_files(include_closed=True):
        if rec["body"].get("id") == ticket_id:
            return p, rec
    return None, None


# ── Public API ────────────────────────────────────────────────────────────────


def read(ticket_id: str) -> Optional[dict]:
    """Return the ticket body for ``ticket_id`` (active or closed), or None."""
    _, rec = _find(ticket_id)
    return rec["body"] if rec else None


def list(status_filter: Optional[str] = None, include_closed: bool = False) -> "builtins.list":  # noqa: F821
    """Return ticket bodies. In-flight by default.

    Filters by ``body.status`` (NOT directory membership): a crash-straggler — a
    closed-status file still in ``tickets/`` — is correctly excluded from the
    in-flight view. Pass ``include_closed=True`` to include terminal tickets.
    """
    out = []
    seen = set()
    for _, rec in _iter_files(include_closed=include_closed):
        body = rec["body"]
        tid = body.get("id")
        status = body.get("status")
        if tid in seen:
            continue
        if not include_closed and status in TERMINAL_STATUSES:
            continue  # straggler — terminal status overrides directory location
        if status_filter is not None and status != status_filter:
            continue
        seen.add(tid)
        out.append(body)
    return out


def _body_eq_ignoring_updated_at(a: dict, b: dict) -> bool:
    """Equal modulo ``updated_at`` — lets _put skip churn-free writes."""
    aa = {k: v for k, v in a.items() if k != "updated_at"}
    bb = {k: v for k, v in b.items() if k != "updated_at"}
    return aa == bb


# Public alias: cc_queue._save reuses this so the "what counts as unchanged"
# definition lives in exactly ONE place (the chokepoint), not duplicated per
# caller — the load-bearing equality must not drift between modules.
def body_eq_ignoring_updated_at(a: dict, b: dict) -> bool:
    """Whether two ticket bodies are equal ignoring ``updated_at`` (churn check)."""
    return _body_eq_ignoring_updated_at(a, b)


def _put(body: dict) -> str:
    """Lock-free status-aware upsert + route. CALLER MUST HOLD ``_mutation_lock``.

    The SINGLE mover. A terminal-status body lives in ``closed/``, non-terminal in
    ``tickets/``. Existing ticket → rewrite in place, then (only if status now implies
    a different dir) atomically ``os.replace`` it across — never in two dirs at once.
    NO-OP when the on-disk body is byte-identical ignoring ``updated_at`` AND already
    in the right dir, so ``_save(all_tasks)`` rewrites only genuinely-changed tickets
    (no 2088-file churn). Does NOT stamp ``updated_at`` — the granular mutators do
    that (they know they changed something); write() is a pure persist.
    """
    if not body.get("id"):
        raise ValueError("ticket must have an 'id'")
    tid = body["id"]
    terminal = body.get("status") in TERMINAL_STATUSES
    if terminal and not body.get("completed_at"):
        # any path that terminalizes a ticket stamps completed_at (advisor: one
        # invariant regardless of whether it arrived via close/set_status/write)
        body["completed_at"] = _now_iso()
    target_dir = _closed_dir() if terminal else _tickets_dir()
    path, rec = _find(tid)
    if rec is not None:
        in_right_dir = path.parent.resolve() == target_dir.resolve()
        if in_right_dir and _body_eq_ignoring_updated_at(rec["body"], body):
            return str(path)  # churn-free no-op
        new_rec = dict(rec)
        new_rec["body"] = body
        _atomic_write(path, new_rec)               # 1) durable in place
        if not in_right_dir:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / path.name
            os.replace(path, dest)                 # 2) atomic move across dirs
            log.info("ticket_store: %s -> %s/%s (status=%s)",
                     tid, target_dir.name, dest.name, body.get("status"))
            _forensic("ticket_moved", {"id": tid, "to_dir": target_dir.name,
                                       "status": body.get("status")})
            return str(dest)
        return str(path)
    target_dir.mkdir(parents=True, exist_ok=True)
    record = _envelope(body)
    newpath = target_dir / (record["id"] + ".json")
    _atomic_write(newpath, record)
    log.info("ticket_store: %s created -> %s/%s", tid, target_dir.name, newpath.name)
    _forensic("ticket_created", {"id": tid, "status": body.get("status"),
                                 "dir": target_dir.name})
    return str(newpath)


def write(ticket: dict) -> str:
    """Create or update a ticket (status-aware routing, churn-free). Returns path.

    A pure persist — does NOT stamp ``updated_at`` (the granular mutators do, since
    they know a change occurred). A terminal-status body routes to ``closed/``;
    non-terminal to ``tickets/``. Writing an unchanged body is a no-op.
    """
    with _mutation_lock():
        return _put(dict(ticket))


def set_worker(ticket_id: str, worker: Optional[str]) -> str:
    """Assign (or clear) the worker on an in-flight ticket. Returns file path."""
    with _mutation_lock():
        path, rec = _find(ticket_id)
        if rec is None:
            raise KeyError(ticket_id)
        body = rec["body"]
        if body.get("status") in TERMINAL_STATUSES:
            log.warning("ticket_store: set_worker on terminal %s ignored", ticket_id)
            return str(path)
        old = body.get("worker")
        body["worker"] = worker
        body["updated_at"] = _now_iso()
        log.info("ticket_store: %s worker %s -> %s", ticket_id, old, worker)
        _forensic("worker_assigned", {"id": ticket_id, "from": old, "to": worker})
        return _put(body)


def set_status(ticket_id: str, status: str) -> str:
    """Transition status. Terminal statuses delegate to ``close`` so the close-move +
    completed_at stamping happen on one path."""
    if status in TERMINAL_STATUSES:
        return close(ticket_id, result=None, status=status)
    with _mutation_lock():
        path, rec = _find(ticket_id)
        if rec is None:
            raise KeyError(ticket_id)
        body = rec["body"]
        if body.get("status") in TERMINAL_STATUSES:
            log.warning("ticket_store: set_status on terminal %s ignored", ticket_id)
            return str(path)
        old = body.get("status")
        body["status"] = status
        body["updated_at"] = _now_iso()
        log.info("ticket_store: %s status %s -> %s", ticket_id, old, status)
        _forensic("status_transition", {"id": ticket_id, "from": old, "to": status,
                                        "via": "set_status"})
        return _put(body)


def close(ticket_id: str, result: Optional[str] = None, status: str = "closed") -> str:
    """Terminate a ticket: stamp completed_at/result, then route to ``closed/`` via
    the single mover (_put). Returns dest path.

    ONE terminal-move path — a terminal status reached through ANY entry (close,
    set_status, or a terminal-status write) lands in ``closed/`` with completed_at
    set, via _put's atomic cross-dir move (never in two dirs at once).
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"close status must be terminal {sorted(TERMINAL_STATUSES)}, got {status!r}")
    with _mutation_lock():
        path, rec = _find(ticket_id)
        if rec is None:
            raise KeyError(ticket_id)
        body = rec["body"]
        old = body.get("status")
        body["status"] = status
        if result is not None:
            body["result"] = result
        body["completed_at"] = _now_iso()
        body["updated_at"] = body["completed_at"]
        log.info("ticket_store: %s closing (status=%s)", ticket_id, status)
        _forensic("ticket_closed", {"id": ticket_id, "from": old, "to": status,
                                    "has_result": result is not None})
        return _put(body)


def conditional_update(ticket_id: str, *, expect_current, mutate) -> Optional[str]:
    """Race-safe check-and-set: the filesystem analogue of an atomic
    ``UPDATE ... WHERE status = expect_current``.

    Acquires ``_mutation_lock``, reads the LIVE on-disk body, and proceeds only
    if its status matches ``expect_current`` (a single status string, or a
    set/tuple/list of acceptable statuses). When it matches, ``mutate(body)`` is
    applied UNDER THE LOCK and the result persisted via the single mover (_put),
    stamping ``updated_at``.

    Because the callback receives the live body inside the lock, there is no
    read-then-write TOCTOU: callers never pre-read to compute fields, so a
    concurrent transition can't slip between a caller's read and this write.

    Returns the file path on success; ``None`` if the status precondition was not
    met (no write performed). Raises ``KeyError`` if the ticket does not exist —
    callers that need to distinguish "missing" from "wrong status" rely on this
    split (e.g. cc_queue.cmd_dispatch's two distinct errors).
    """
    with _mutation_lock():
        path, rec = _find(ticket_id)
        if rec is None:
            raise KeyError(ticket_id)
        current = rec["body"].get("status")
        ok = (current == expect_current if isinstance(expect_current, str)
              else current in expect_current)
        if not ok:
            return None
        new_body = mutate(dict(rec["body"]))
        new_body["updated_at"] = _now_iso()
        _forensic("status_transition", {"id": ticket_id, "from": current,
                                        "to": new_body.get("status"),
                                        "via": "conditional_update"})
        return _put(new_body)


def next_for_worker(worker: Optional[str] = None) -> Optional[dict]:
    """Highest-priority in-flight, workable ticket (optionally for a given worker).

    NOTE: gate-awareness is intentionally NOT handled here (see module docstring) —
    callers that dispatch must apply gate logic. Returns the ticket body or None.
    """
    candidates = []
    for body in list(include_closed=False):
        if body.get("status") not in WORKABLE_STATUSES:
            continue
        if worker is not None and body.get("worker") not in (None, worker):
            continue
        candidates.append(body)
    candidates.sort(key=lambda b: (b.get("priority") or 0.0), reverse=True)
    return candidates[0] if candidates else None
