"""
pursuits.py — Pursuit layer: goal-bound behavioral units above engrams.

A Pursuit holds the commitment-to-completion arc of an action that cannot
be captured by a single engram firing. See:
  lab/design_docs/pursuit_layer.md         — the concept and biology
  lab/design_docs/pursuit_programming.md   — when to wrap, how to wrap

MVP scope (T-single-pursuit-test-case):
  - Pursuit dataclass + in-process registry (TWM-style)
  - spawn() fires commitment dopamine
  - evaluate_completion() runs completion_test, fires outcome dopamine
  - suspend()/resume() for nesting
  - subscriber hook so other engrams can react to dopamine events

Anticipation wiring (T-anticipation-slice2-pursuit-emit):
  - spawn() pushes an Anticipation onto the AnticipationBus and stashes
    its id on the Pursuit. Producer side of the want-signal loop.
  - evaluate_completion() settles the bus + register_outcome with the
    actual delta (1.0 for completion, -0.5 for abandonment) so the
    Anticipator's predictor learns from each pursuit's outcome.

Gate: IGOR_PURSUITS_ENABLED (default false). When disabled, spawn() is a
no-op that returns a Pursuit with status=disabled — callers can ignore it
safely. Anticipation wiring also short-circuits in disabled mode (no
push, no settle) so test isolation stays clean.

Deferred to follow-up tickets: Postgres persistence, staleness-based
abandonment, milieu integration, cascade-side Pursuit awareness.

Updated 2026-05-03.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..igor_base import get_logger
from . import anticipator

log = get_logger(__name__)


def enabled() -> bool:
    return os.getenv("IGOR_PURSUITS_ENABLED", "false").lower() == "true"


Predicate = Callable[[dict], bool]


@dataclass
class DopamineEvent:
    kind: str  # "commitment" | "subgoal" | "completion" | "abandonment"
    ts: float
    magnitude: float
    pursuit_id: str
    note: str = ""


@dataclass
class Pursuit:
    id: str
    name: str
    entry_stimulus: dict
    goal_facia: Predicate
    commitment_ts: float
    parent_pursuit: Optional[str] = None
    sub_pursuits: list[str] = field(default_factory=list)
    actions_taken: list[dict] = field(default_factory=list)
    status: str = "active"  # pending|active|suspended|completed|abandoned|disabled
    dopamine_trace: list[DopamineEvent] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # T-anticipation-slice2: handle for the Anticipation pushed onto the
    # AnticipationBus at spawn time. Settled at completion/abandonment.
    anticipation_id: Optional[str] = None

    def record_action(self, action_ref: dict) -> None:
        """Attribute an engram activation to this Pursuit."""
        self.actions_taken.append({"ts": time.time(), **action_ref})

    def suspend(self) -> None:
        if self.status == "active":
            self.status = "suspended"

    def resume(self) -> None:
        if self.status == "suspended":
            self.status = "active"

    def evaluate_completion(self, state: dict) -> str:
        """Run goal_facia against state. Fire completion or abandonment
        dopamine accordingly. Returns the new status."""
        if self.status in ("completed", "abandoned", "disabled"):
            return self.status
        try:
            done = bool(self.goal_facia(state))
        except Exception as exc:
            log.info("pursuit %s goal_facia raised: %s", self.id, exc)
            done = False
        if done:
            self.status = "completed"
            _registry.emit(
                DopamineEvent(
                    kind="completion",
                    ts=time.time(),
                    magnitude=1.0,
                    pursuit_id=self.id,
                    note=self.name,
                )
            )
            self._settle_anticipation(actual_delta=1.0)
            self._signal_parent("subgoal")
        else:
            self.status = "abandoned"
            _registry.emit(
                DopamineEvent(
                    kind="abandonment",
                    ts=time.time(),
                    magnitude=-0.5,
                    pursuit_id=self.id,
                    note=self.name,
                )
            )
            self._settle_anticipation(actual_delta=-0.5)
        return self.status

    def _settle_anticipation(self, actual_delta: float) -> None:
        """T-anticipation-slice2: close the loop with the Anticipator.

        Removes the anticipation from the bus and feeds the actual
        outcome to register_outcome so the predictor's rolling-mean
        learns from this pursuit. Silent no-op when no anticipation is
        attached (e.g. disabled-mode pursuit, or test fixture that
        bypassed spawn()).
        """
        if not self.anticipation_id:
            return
        ant = anticipator.get_bus().settle(self.anticipation_id)
        if ant is None:
            log.info(
                "pursuit %s anticipation %s missing from bus at settle time",
                self.id,
                self.anticipation_id,
            )
            return
        anticipator.get_anticipator().register_outcome(ant, actual_delta)

    def _signal_parent(self, kind: str) -> None:
        if not self.parent_pursuit:
            return
        parent = _registry.get(self.parent_pursuit)
        if parent is None:
            return
        _registry.emit(
            DopamineEvent(
                kind=kind,
                ts=time.time(),
                magnitude=0.5,
                pursuit_id=parent.id,
                note=f"child:{self.name}",
            )
        )


class _Registry:
    """In-process TWM-style store of active Pursuits, plus dopamine subscribers."""

    def __init__(self) -> None:
        self._pursuits: dict[str, Pursuit] = {}
        self._subscribers: list[Callable[[DopamineEvent], None]] = []

    def register(self, p: Pursuit) -> None:
        self._pursuits[p.id] = p

    def get(self, pid: str) -> Optional[Pursuit]:
        return self._pursuits.get(pid)

    def active(self) -> list[Pursuit]:
        return [p for p in self._pursuits.values() if p.status == "active"]

    def all(self) -> list[Pursuit]:
        return list(self._pursuits.values())

    def subscribe(self, fn: Callable[[DopamineEvent], None]) -> None:
        self._subscribers.append(fn)

    def emit(self, event: DopamineEvent) -> None:
        p = self._pursuits.get(event.pursuit_id)
        if p is not None:
            p.dopamine_trace.append(event)
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception as exc:
                log.info("dopamine subscriber raised: %s", exc)

    def clear(self) -> None:
        """Test helper — wipe registry state between tests."""
        self._pursuits.clear()
        self._subscribers.clear()


_registry = _Registry()


def registry() -> _Registry:
    return _registry


def spawn(
    name: str,
    entry_stimulus: dict,
    goal_facia: Predicate,
    parent_pursuit: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Pursuit:
    """Spawn a Pursuit. Fires commitment dopamine. Returns the object.

    If IGOR_PURSUITS_ENABLED is false, returns a disabled Pursuit — callers
    can still call evaluate_completion/suspend/resume on it; they become
    no-ops. This lets call sites wire up once and flip the gate to enable.
    """
    pid = str(uuid.uuid4())
    if not enabled():
        return Pursuit(
            id=pid,
            name=name,
            entry_stimulus=entry_stimulus,
            goal_facia=goal_facia,
            commitment_ts=time.time(),
            parent_pursuit=parent_pursuit,
            status="disabled",
            metadata=metadata or {},
        )

    p = Pursuit(
        id=pid,
        name=name,
        entry_stimulus=entry_stimulus,
        goal_facia=goal_facia,
        commitment_ts=time.time(),
        parent_pursuit=parent_pursuit,
        metadata=metadata or {},
    )
    _registry.register(p)
    if parent_pursuit:
        parent = _registry.get(parent_pursuit)
        if parent is not None:
            parent.sub_pursuits.append(pid)
            parent.suspend()

    # T-anticipation-slice2: push an Anticipation onto the bus so action
    # selection (slice 3) can bias toward high-want pursuits, and so the
    # Anticipator's predictor learns from each pursuit's actual outcome.
    ant = anticipator.get_anticipator().predict(
        referent_id=pid, referent_type="pursuit"
    )
    anticipator.get_bus().push(ant)
    p.anticipation_id = ant.id

    _registry.emit(
        DopamineEvent(
            kind="commitment",
            ts=p.commitment_ts,
            magnitude=0.7,
            pursuit_id=pid,
            note=name,
        )
    )
    log.info("pursuit spawned: %s (id=%s parent=%s)", name, pid, parent_pursuit)
    return p


def resume_parent(pursuit: Pursuit) -> None:
    """Helper: after a child Pursuit completes, resume its parent if any."""
    if pursuit.parent_pursuit:
        parent = _registry.get(pursuit.parent_pursuit)
        if parent is not None:
            parent.resume()
