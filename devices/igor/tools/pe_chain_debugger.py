"""pe_chain_debugger.py — T-pe-chain-single-step-debugger.

Inject a stimulus, run pe_chain to a named breakpoint, snapshot state, step
forward one engram at a time. The tool that would have caught the
hallucinated-target bug in-flight: at the HYPOTHESIZE pause we'd see
`basket['hypothesis']['file'] = brainstem/core_patterns.py` next to
`basket['ticket_description']` that mentions a completely different file —
the gap is visible immediately instead of surfacing as a vague escalation
several steps later.

Extends T-igor-self-introspection (shipped today, 74d1c59e). Where
self_inspect gives ambient state reads, this gives stepped execution control.

Scope
─────
IN: inject stimulus, pause at a named engram breakpoint (SITUATE, PLAN,
HYPOTHESIZE, IMPLEMENT, OBSERVE, CLOSE), return basket + 7-aspect snapshot;
step_next advances one engram and re-snapshots; abandon clears state.
OUT: modifying engram behavior during debug (read-only stepping); UI
frontend (returns JSON; UI is a separate ticket); replay of past real
sessions (stimulus must be freshly injected).

Engram sequence (canonical for coding work)
──────────────────────────────────────────
ENTRY → CLAIM → READ_TICKET → PLAN → SITUATE → HYPOTHESIZE → IMPLEMENT
      → OBSERVE → CLOSE

Each step is a pe_* function in pe_chain.py. Breakpoint names are
upper-case step names (the part after pe_). The debugger simply calls them
in order, pausing before/after the breakpoint.

Usage
─────
    from wild_igor.igor.tools.pe_chain_debugger import start, step_next

    session = start(
        ticket_id="T-no-sqlite-enforcement",
        breakpoint="HYPOTHESIZE",
    )
    print(session["snapshot"]["basket"]["hypothesis"])

    session = step_next(session["session_id"])  # advances to IMPLEMENT
    print(session["snapshot"]["basket"]["implementation"])
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import pe_chain
from lab.utility_closet.registry import Tool, registry

log = logging.getLogger(__name__)


# ── Engram sequence ───────────────────────────────────────────────────────────


# Canonical sequence for coding work. Step name → pe_chain function.
# Order matches pe_chain.py header steps 1-12 (OBSERVE before HYPOTHESIZE so
# basket["actual"] is populated before the LLM call).
STEPS: list[tuple[str, Callable]] = [
    ("ENTRY", pe_chain.pe_entry_init),
    ("CLAIM", pe_chain.pe_claim),
    ("READ_TICKET", pe_chain.pe_read_ticket),
    ("PLAN", pe_chain.pe_plan),
    ("SITUATE", pe_chain.pe_situate),
    ("OBSERVE", pe_chain.pe_observe),
    ("HYPOTHESIZE", pe_chain.pe_hypothesize),
    ("IMPLEMENT", pe_chain.pe_implement),
]


def step_names() -> list[str]:
    return [name for name, _ in STEPS]


# ── Session storage ───────────────────────────────────────────────────────────


@dataclass
class DebugSession:
    session_id: str
    ticket_id: str
    breakpoint: str
    basket: dict = field(default_factory=dict)
    step_index: int = 0  # next step to run
    history: list[dict] = field(default_factory=list)  # snapshots per step
    created_at: float = field(default_factory=time.time)
    finished: bool = False

    def next_step_name(self) -> str | None:
        if self.step_index >= len(STEPS):
            return None
        return STEPS[self.step_index][0]


_sessions: dict[str, DebugSession] = {}
_sessions_lock = threading.Lock()


def _store(session: DebugSession) -> None:
    with _sessions_lock:
        _sessions[session.session_id] = session


def _get(session_id: str) -> DebugSession | None:
    with _sessions_lock:
        return _sessions.get(session_id)


# ── Snapshot helpers ──────────────────────────────────────────────────────────


def _snapshot(session: DebugSession, last_step: str | None) -> dict:
    """Assemble a pause snapshot: basket + self_inspect aspects."""
    try:
        from .self_inspect import inspect as _inspect
    except Exception:
        _inspect = None  # type: ignore

    aspects = {}
    if _inspect is not None:
        for aspect in (
            "affect",
            "attention",
            "pursuits_active",
        ):
            aspects[aspect] = _inspect(aspect)

    return {
        "session_id": session.session_id,
        "ticket_id": session.ticket_id,
        "step_index": session.step_index,
        "last_step": last_step,
        "next_step": session.next_step_name(),
        "breakpoint": session.breakpoint,
        "finished": session.finished,
        "basket": copy.deepcopy(session.basket),
        "aspects": aspects,
        "ts": time.time(),
    }


# ── Step execution ────────────────────────────────────────────────────────────


def _run_until_breakpoint(session: DebugSession) -> dict:
    """Run pe_* steps from session.step_index up to (but not past) the
    breakpoint. Records a snapshot after each step runs. Returns the
    final snapshot.

    A breakpoint of 'END' runs the whole chain. An unknown breakpoint name
    still runs through — the debugger has exhausted its sequence.
    """
    bp = (session.breakpoint or "").upper()
    last_step = None
    while session.step_index < len(STEPS):
        name, fn = STEPS[session.step_index]
        # Stop BEFORE running the breakpoint step? No — the user wants the
        # state AFTER the step they named, so they can see what it produced.
        try:
            session.basket = fn(session.basket)
        except Exception as e:
            session.basket["debugger_error"] = f"{name}: {type(e).__name__}: {e}"
            log.warning(
                "pe_chain_debugger: step %s raised %s: %s", name, type(e).__name__, e
            )
        last_step = name
        session.step_index += 1
        snap = _snapshot(session, last_step)
        session.history.append(snap)
        if name.upper() == bp:
            break
    if session.step_index >= len(STEPS):
        session.finished = True
    return _snapshot(session, last_step)


# ── Public API ────────────────────────────────────────────────────────────────


def start(
    ticket_id: str,
    breakpoint: str = "HYPOTHESIZE",
    initial_basket: dict | None = None,
    repo_root: str | None = None,
) -> dict:
    """Begin a debug session for ticket_id; run until breakpoint; return snapshot.

    initial_basket can seed extra context (e.g. pre-populated ticket fields
    for unit testing). Otherwise the basket starts empty and pe_entry_init
    derives context from the active GOAL.

    repo_root, when supplied, sets the IGOR_PE_CHAIN_REPO_ROOT env var for
    this process so pe_chain reads from a worktree (or other path) instead
    of the default ~/TheIgors checkout. Required for replay-old cert walks
    where the harness runs against an older commit's tree.
    """
    import os

    # T-cert-debugger-env-mirror: load Igor's switches.cfg into os.environ on
    # debugger entry so standalone harnesses route through the same model
    # (cloud Qwen 32B vs local Ollama 7B) as Igor's autonomous pe_chain. The
    # silent-route-to-different-model failure mode burned 7+ cert attempts.
    try:
        from ..env_sync import load_igor_env_into_environ

        applied = load_igor_env_into_environ()
        if applied:
            log.info(
                "pe_chain_debugger.start: loaded %d vars from instance cfg "
                "(IGOR_CLOUD_PROGRAMMING=%s)",
                len(applied),
                os.environ.get("IGOR_CLOUD_PROGRAMMING", "unset"),
            )
    except Exception as exc:
        log.warning("pe_chain_debugger.start: cfg load failed (non-fatal): %s", exc)

    valid = {s[0] for s in STEPS}
    if breakpoint.upper() not in valid and breakpoint.upper() != "END":
        return {
            "ok": False,
            "error": f"unknown breakpoint '{breakpoint}' — valid: {sorted(valid)} or END",
        }
    if repo_root is not None:
        os.environ["IGOR_PE_CHAIN_REPO_ROOT"] = str(repo_root)
        log.info("pe_chain_debugger.start: repo_root override set to %s", repo_root)
    sid = f"dbg-{uuid.uuid4().hex[:12]}"
    basket = dict(initial_basket or {})
    basket.setdefault("ticket_id", ticket_id)
    session = DebugSession(
        session_id=sid,
        ticket_id=ticket_id,
        breakpoint=breakpoint.upper(),
        basket=basket,
    )
    _store(session)
    snapshot = _run_until_breakpoint(session)
    return {"ok": True, "session_id": sid, "snapshot": snapshot}


def step_next(session_id: str) -> dict:
    """Advance one engram; return snapshot after that step runs."""
    session = _get(session_id)
    if session is None:
        return {"ok": False, "error": f"unknown session '{session_id}'"}
    if session.finished:
        return {
            "ok": True,
            "session_id": session_id,
            "snapshot": _snapshot(session, None),
            "note": "session already finished — no more steps",
        }
    name, fn = STEPS[session.step_index]
    try:
        session.basket = fn(session.basket)
    except Exception as e:
        session.basket["debugger_error"] = f"{name}: {type(e).__name__}: {e}"
        log.warning("pe_chain_debugger step_next: %s raised %s", name, e)
    session.step_index += 1
    snap = _snapshot(session, name)
    session.history.append(snap)
    if session.step_index >= len(STEPS):
        session.finished = True
    return {"ok": True, "session_id": session_id, "snapshot": snap}


def snapshot(session_id: str) -> dict:
    """Return current snapshot without advancing."""
    session = _get(session_id)
    if session is None:
        return {"ok": False, "error": f"unknown session '{session_id}'"}
    last = session.history[-1]["last_step"] if session.history else None
    return {"ok": True, "session_id": session_id, "snapshot": _snapshot(session, last)}


def history(session_id: str) -> dict:
    """Return the full per-step snapshot history."""
    session = _get(session_id)
    if session is None:
        return {"ok": False, "error": f"unknown session '{session_id}'"}
    return {
        "ok": True,
        "session_id": session_id,
        "steps": len(session.history),
        "history": session.history,
    }


def abandon(session_id: str) -> dict:
    """Clear a debug session."""
    with _sessions_lock:
        removed = _sessions.pop(session_id, None)
    return {"ok": removed is not None, "session_id": session_id}


def list_sessions() -> dict:
    """Return a summary of active debug sessions."""
    with _sessions_lock:
        return {
            "ok": True,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "ticket_id": s.ticket_id,
                    "breakpoint": s.breakpoint,
                    "step_index": s.step_index,
                    "finished": s.finished,
                    "steps_recorded": len(s.history),
                    "age_s": int(time.time() - s.created_at),
                }
                for s in _sessions.values()
            ],
        }


# ── Tool wrappers (string-in/string-out for registry) ─────────────────────────


def _tool_start(ticket_id: str = "", breakpoint: str = "HYPOTHESIZE") -> str:
    import json

    if not ticket_id:
        return json.dumps({"ok": False, "error": "ticket_id required"})
    return json.dumps(start(ticket_id, breakpoint), default=str, ensure_ascii=False)


def _tool_step_next(session_id: str = "") -> str:
    import json

    if not session_id:
        return json.dumps({"ok": False, "error": "session_id required"})
    return json.dumps(step_next(session_id), default=str, ensure_ascii=False)


registry.register(
    Tool(
        name="pe_chain_debug_start",
        description=(
            "Begin a pe_chain debug session — inject a ticket_id stimulus, "
            "run the engram sequence up to a named breakpoint, and return the "
            "basket + inspect snapshot at the pause. Breakpoints: ENTRY, CLAIM, "
            "READ_TICKET, PLAN, SITUATE, HYPOTHESIZE (default), IMPLEMENT, "
            "OBSERVE, or END (run the whole chain)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket to debug (e.g. T-foo)",
                },
                "breakpoint": {
                    "type": "string",
                    "description": ("Engram name to pause at (default HYPOTHESIZE)"),
                },
            },
            "required": ["ticket_id"],
        },
        fn=_tool_start,
    )
)


registry.register(
    Tool(
        name="pe_chain_debug_step",
        description=(
            "Advance a pe_chain debug session by one engram step. Returns "
            "the basket + inspect snapshot after the step runs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The debug session id returned by pe_chain_debug_start",
                }
            },
            "required": ["session_id"],
        },
        fn=_tool_step_next,
    )
)


__all__ = [
    "start",
    "step_next",
    "snapshot",
    "history",
    "abandon",
    "list_sessions",
    "step_names",
    "STEPS",
    "DebugSession",
]
