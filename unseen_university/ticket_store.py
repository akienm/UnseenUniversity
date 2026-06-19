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

from unseen_university._uu_root import uu_root
from unseen_university.gate_logic import TERMINAL_STATUSES

log = logging.getLogger(__name__)

# Stored statuses a worker may be dispatched onto. Display taxonomy (READY etc.)
# is derived elsewhere; these are the literal stored strings.
WORKABLE_STATUSES = {"sprint", "assigned"}

# Default envelope link buckets (mirrors memory_emit).
_LINK_KEYS = ("goals", "decisions", "tickets", "commits", "whys")


# ── Paths (env read dynamically so tests can point UU_MEMORY_ROOT at a tmp dir) ──


def _memory_root() -> Path:
    val = os.environ.get("UU_MEMORY_ROOT")
    if val:
        return Path(val)
    return Path(uu_root()) / "devlab" / "runtime" / "memory"


def _tickets_dir() -> Path:
    return _memory_root() / "tickets"


def _closed_dir() -> Path:
    return _tickets_dir() / "closed"


def _lock_path() -> Path:
    return _tickets_dir() / ".queue.lock"


# ── Primitives ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return {
        "id": stem,
        "emitter": emitter,
        "namespace": ns,
        "category": "tickets",
        "kind": "ticket",
        "emitted_at": now.astimezone(timezone.utc).isoformat(),
        "links": links,
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


def write(ticket: dict) -> str:
    """Create or update a ticket. Returns the file path.

    Existing ticket (by ``body.id``) → in-place atomic rewrite of the SAME file
    (envelope preserved, only ``body`` swapped + ``updated_at`` bumped), so the
    active dir never accumulates duplicate envelopes per id. New ticket → a fresh
    envelope file in ``tickets/``.
    """
    if not ticket.get("id"):
        raise ValueError("ticket must have an 'id'")
    tid = ticket["id"]
    with _mutation_lock():
        path, rec = _find(tid)
        if rec is not None:
            rec["body"] = dict(ticket)
            rec["body"]["updated_at"] = _now_iso()
            _atomic_write(path, rec)
            log.info("ticket_store: %s updated (status=%s)", tid, ticket.get("status"))
            return str(path)
        record = _envelope(dict(ticket))
        _tickets_dir().mkdir(parents=True, exist_ok=True)
        newpath = _tickets_dir() / (record["id"] + ".json")
        _atomic_write(newpath, record)
        log.info("ticket_store: %s created -> %s", tid, newpath.name)
        return str(newpath)


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
        _atomic_write(path, rec)
        log.info("ticket_store: %s worker %s -> %s", ticket_id, old, worker)
        return str(path)


def set_status(ticket_id: str, status: str) -> str:
    """Transition status. Terminal statuses delegate to ``close`` (so the
    active-dir-holds-only-in-flight invariant holds regardless of entry point)."""
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
        _atomic_write(path, rec)
        log.info("ticket_store: %s status %s -> %s", ticket_id, old, status)
        return str(path)


def close(ticket_id: str, result: Optional[str] = None, status: str = "closed") -> str:
    """Terminate a ticket and move it to ``tickets/closed/``. Returns dest path.

    Crash-safe: the body is rewritten in place with the terminal status FIRST, then
    the file is moved with a single atomic ``os.replace``. The file is never in both
    dirs; a crash before the move leaves a closed-status file in ``tickets/``, which
    callers exclude via status, not location.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"close status must be terminal {sorted(TERMINAL_STATUSES)}, got {status!r}")
    with _mutation_lock():
        path, rec = _find(ticket_id)
        if rec is None:
            raise KeyError(ticket_id)
        body = rec["body"]
        body["status"] = status
        if result is not None:
            body["result"] = result
        body["completed_at"] = _now_iso()
        body["updated_at"] = body["completed_at"]
        # 1) durable in place — even if the rename never happens, status is terminal
        _atomic_write(path, rec)
        # 2) atomic move into closed/ (single rename; never in two places at once)
        _closed_dir().mkdir(parents=True, exist_ok=True)
        dest = _closed_dir() / path.name
        if path.resolve() != dest.resolve():
            os.replace(path, dest)
        log.info("ticket_store: %s closed (status=%s) -> closed/%s", ticket_id, status, dest.name)
        return str(dest)


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
