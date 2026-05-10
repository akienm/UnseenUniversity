"""
COA — Center of Attention.

A COA bundles a NarrativeEngine with its own spawn/idle state so that
multiple COAs can run concurrently in one process.

Root COA: created by Igor.__init__; its tick() is called each main-loop
iteration. All existing behavior is unchanged from before the extraction.

Background COA: created by COA.spawn(). Gets a self-managed tick loop
thread. Dissolves (stops ticking) when its task_queue empties. The root
COA continues unaffected.

CPU gate: spawn() checks psutil CPU% against IGOR_COA_CPU_GATE (default
60, percent). A spawn is blocked when the box is already busy.

Intra-box milieu propagation: all COAs share the process-level milieu
singleton. Their NE runs contribute NE-state to the same Milieu instance,
which in turn contributes to the shared global-milieu file
(paths().milieu). Same mechanism as cross-box propagation. No separate
Milieu instance per COA is needed for this phase.

Back-reference pattern: COA holds a reference to its owning Igor instance
(``_igor``) so tick() can read _is_processing and delegate
experiment_scheduler.tick() without duplicating those objects. Background
COAs pass the same back-reference; _experiment_scheduler.tick() is
intentionally shared (only one experiment tick per root-loop iteration).
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any

from ..igor_base import IgorBase

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from ..cognition.narrative_engine import NarrativeEngine as _NE


# ------------------------------------------------------------------
# CPU gate helper
# ------------------------------------------------------------------

_CPU_GATE_DEFAULT = 60.0  # percent


def _cpu_percent_now() -> float:
    """Return current CPU% (1s measurement). Returns 0.0 if psutil unavailable."""
    try:
        import psutil

        return psutil.cpu_percent(interval=1.0)
    except Exception:
        return 0.0


def _cpu_gate_ok() -> bool:
    """True if CPU is below the spawn gate threshold."""
    gate = float(os.getenv("IGOR_COA_CPU_GATE", str(_CPU_GATE_DEFAULT)))
    return _cpu_percent_now() < gate


# ------------------------------------------------------------------
# COA
# ------------------------------------------------------------------


class COA(IgorBase):
    """Center of Attention — NE + TWM attentional unit for one cognitive focus."""

    def __init__(self, cortex: "Cortex", instance_id: str, igor: object) -> None:
        from .narrative_engine import NarrativeEngine

        self.ne: _NE = NarrativeEngine(cortex, instance_id)
        self._cortex = cortex
        self._igor = igor  # back-ref: _is_processing, _experiment_scheduler
        self._instance_id = instance_id

        self._ne_thread: threading.Thread | None = None
        self._ne_spawn_lock: threading.Lock = threading.Lock()
        self._ne_last_twm_fingerprint: tuple[int, int] = (0, 0)
        self._ne_last_run_time: float = 0.0
        self._last_ne_valence: float = 0.0

        # Background-COA state (unused in root COA)
        self._task_queue: list[Any] = []
        self._bg_thread: threading.Thread | None = None
        self._is_background: bool = False

    # ------------------------------------------------------------------
    # Spawn primitive
    # ------------------------------------------------------------------

    def spawn(self, task_queue: list[Any] | None = None) -> "COA | None":
        """
        Spawn a background COA to work through task_queue.

        Returns the new COA, or None if the CPU gate blocks the spawn.
        The child runs its own tick loop in a daemon thread and dissolves
        when its task_queue is exhausted.

        CPU gate: blocked when CPU% >= IGOR_COA_CPU_GATE (default 60).
        """
        if not _cpu_gate_ok():
            return None

        child_id = f"{self._instance_id}-bg-{int(time.monotonic() * 1000) % 100_000}"
        child = COA(self._cortex, child_id, self._igor)
        child._task_queue = list(task_queue or [])
        child._is_background = True
        child._start_background_loop()
        return child

    def _start_background_loop(self) -> None:
        """Start the self-managed tick loop for background COAs."""

        def _loop() -> None:
            while self._task_queue:
                self.tick()
                time.sleep(0.5)
            # task_queue drained — COA dissolves; thread exits naturally

        self._bg_thread = threading.Thread(
            target=_loop, daemon=True, name=f"coa-bg-{id(self)}"
        )
        self._bg_thread.start()

    @property
    def is_alive(self) -> bool:
        """True while background loop is still running (always True for root COA)."""
        if not self._is_background:
            return True
        return self._bg_thread is not None and self._bg_thread.is_alive()

    # ------------------------------------------------------------------
    # Main-loop tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """
        Fire the Narrative Engine in a background daemon thread.

        If NE is already running (Ollama is slow), skip — don't stack calls.
        The NE is stateless between runs (all state in Postgres), so this is safe.

        Idle gate: skip if TWM hasn't changed since last run AND < 2min cooldown.
        Lock: prevents double-fire race when two callers hit simultaneously.
        """
        import time as _t

        from ..cognition import milieu as milieu_mod
        from ..cognition.forensic_logger import log_error

        try:
            from .daemon_supervisor import supervisor as _sup

            _sup.heartbeat("ne-worker")
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug(
                "daemon_supervisor.heartbeat(ne-worker) failed: %s", e
            )

        if self._ne_thread is not None and self._ne_thread.is_alive():
            return  # Already running

        if not self._ne_spawn_lock.acquire(blocking=False):
            return

        try:
            _now = _t.monotonic()
            _COOLDOWN = 120.0
            try:
                _obs = self._cortex.twm_count()
                _max_id = self._cortex.twm_max_id()
                _fingerprint = (_obs, _max_id)
            except Exception:
                _fingerprint = (0, 0)

            _same_state = _fingerprint == self._ne_last_twm_fingerprint
            _in_cooldown = (_now - self._ne_last_run_time) < _COOLDOWN
            if _same_state and _in_cooldown:
                return

            self._ne_last_twm_fingerprint = _fingerprint
            self._ne_last_run_time = _now

            igor = self._igor

            def _ne_worker() -> None:
                _waited = 0.0
                while getattr(igor, "_is_processing", False) and _waited < 10.0:
                    _t.sleep(0.5)
                    _waited += 0.5
                try:
                    result = self.ne.run(verbose=False)
                    if result:
                        _ne_state = result.get("internal_state", {})
                        _m = milieu_mod.get()
                        if _ne_state and _m:
                            _m.ingest_ne_state(_ne_state)
                        if _ne_state:
                            try:
                                self._last_ne_valence = float(
                                    _ne_state.get("valence", 0.0)
                                )
                            except (TypeError, ValueError) as _bare_e:
                                log_error(
                                    kind="BARE_EXCEPT",
                                    detail=f"wild_igor/igor/cognition/coa.py: {_bare_e}",
                                )
                            # Append psych snapshot to longitudinal log
                            try:
                                import json as _json
                                from ..paths import paths as _paths

                                _psych_entry = {
                                    "ts": _t.time(),
                                    "valence": self._last_ne_valence,
                                    "arousal": float(_ne_state.get("arousal", 0.0)),
                                    "notes": str(_ne_state.get("notes", "")),
                                }
                                _psych_log = _paths().logs / "igor_psych.jsonl"
                                _psych_log.parent.mkdir(parents=True, exist_ok=True)
                                with open(_psych_log, "a") as _f:
                                    _f.write(_json.dumps(_psych_entry) + "\n")
                            except Exception as _psych_e:
                                log_error(
                                    kind="PSYCH_LOG", detail=f"coa.py: {_psych_e}"
                                )
                    else:
                        # NE produced no result — escalate rather than go mute.
                        # D-escalate-as-default-2026-05-10: escalate is the
                        # default fallback when habit inventory exhausts.
                        try:
                            from .escalate import escalate_to_channel as _esc

                            _esc(
                                f"[NE] cycle produced no result — Igor may be stuck. "
                                f"Last valence: {self._last_ne_valence:.2f}. "
                                "Nothing actionable in TWM — watch-question scan runs "
                                "next lever-watcher cycle.",
                                dedup_key="ne-empty-result",
                            )
                        except Exception as _esc_e:
                            log_error(kind="NE_ESCALATE", detail=f"coa.py: {_esc_e}")
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/coa.py: {_bare_e}",
                    )
                # Annotate pending engrams (batch_size=2 to stay within budget)
                try:
                    from ..memory.purpose_annotator import (
                        annotate_pending as _annotate_pending,
                    )

                    _n_annotated = _annotate_pending(self._cortex, batch_size=2)
                    if _n_annotated > 0:
                        import logging as _logging

                        _logging.getLogger(__name__).info(
                            "purpose_annotator: annotated %d engrams", _n_annotated
                        )
                except Exception as _ann_e:
                    log_error(kind="PURPOSE_ANNOTATOR", detail=f"coa.py: {_ann_e}")
                # Scan watch_problems for incoming levers (D-escalate-as-default-2026-05-10)
                try:
                    from .watch_problems import lever_watcher as _lever_watcher

                    _lever_watcher()
                except Exception as _lw_e:
                    log_error(kind="LEVER_WATCHER", detail=f"coa.py: {_lw_e}")
                try:
                    _exp_sched = getattr(igor, "_experiment_scheduler", None)
                    if _exp_sched is not None:
                        _exp = _exp_sched.tick()
                        if _exp:
                            import logging

                            logging.getLogger(__name__).info(
                                "experiment_tick: ran %s → %s",
                                _exp.experiment_id,
                                _exp.status.value,
                            )
                except Exception as _exp_e:
                    log_error(
                        kind="EXPERIMENT_TICK",
                        detail=f"coa.py ne_worker: {_exp_e}",
                    )

            self._ne_thread = threading.Thread(
                target=_ne_worker, daemon=True, name="ne-worker"
            )
            self._ne_thread.start()
            try:
                from .daemon_supervisor import supervisor as _sup

                _sup.register(
                    "ne-worker",
                    self._ne_thread,
                    one_shot=True,
                    staleness_threshold_secs=600.0,
                )
            except Exception as _exc:
                log_error(kind="SILENT_EXCEPT", detail=f"coa.py:tick: {_exc}")
        finally:
            self._ne_spawn_lock.release()


def read_psych_log(days: int = 7) -> list[dict]:
    """Return psych log entries from the last N days, newest last."""
    import json as _json
    import time as _time

    from ..paths import paths as _paths

    cutoff = _time.time() - days * 86400
    path = _paths().logs / "igor_psych.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                if entry.get("ts", 0) >= cutoff:
                    entries.append(entry)
            except _json.JSONDecodeError:
                continue
    return entries
