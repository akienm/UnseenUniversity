"""
COA — Center of Attention.

A COA bundles a NarrativeEngine with its own spawn/idle state so that
multiple COAs can run concurrently in one process. For the current
single-COA case the root COA object holds exactly the state that used to
live on Igor as loose attributes (_ne_thread, _ne_spawn_lock, etc.) and
the _run_ne_background() method.

tick() is the former _run_ne_background() from main.py. Behavior is
identical to the pre-extraction single-COA implementation — this refactor
is a structural no-op. The spawn primitive (multiple COAs per process)
is in T-coa-spawn-primitive.

Back-reference pattern: COA holds a reference to its owning Igor instance
(``_igor``) so tick() can read _is_processing and delegate
experiment_scheduler.tick() without duplicating those objects.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from ..cognition.narrative_engine import NarrativeEngine as _NE


class COA:
    """Center of Attention — NE + TWM attentional unit for one cognitive focus."""

    def __init__(self, cortex: "Cortex", instance_id: str, igor: object) -> None:
        from .narrative_engine import NarrativeEngine

        self.ne: _NE = NarrativeEngine(cortex, instance_id)
        self._cortex = cortex
        self._igor = igor  # back-ref: _is_processing, _experiment_scheduler

        self._ne_thread: threading.Thread | None = None
        self._ne_spawn_lock: threading.Lock = threading.Lock()
        self._ne_last_twm_fingerprint: tuple[int, int] = (0, 0)
        self._ne_last_run_time: float = 0.0
        self._last_ne_valence: float = 0.0

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
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/coa.py: {_bare_e}",
                    )
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
