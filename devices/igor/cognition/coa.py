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

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from ..igor_base import IgorBase

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from ..cognition.narrative_engine import NarrativeEngine as _NE

_coa_log = logging.getLogger(__name__)


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
        self._ne_cycle_counter: int = 0
        self._ne_stuck_count: int = 0  # consecutive no-result cycles; reset on result

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

        try:
            from .daemon_supervisor import supervisor as _sup

            _sup.heartbeat("ne-worker")
        except Exception as e:
            self.log.debug("daemon_supervisor.heartbeat(ne-worker) failed: %s", e)

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
            self.log.info(
                "NE_TICK twm_count=%d twm_max_id=%d", _fingerprint[0], _fingerprint[1]
            )

            igor = self._igor

            def _ne_worker() -> None:
                _waited = 0.0
                while getattr(igor, "_is_processing", False) and _waited < 10.0:
                    _t.sleep(0.5)
                    _waited += 0.5
                result = None  # initialized here; set after ne.run() succeeds
                try:
                    # Check NE's own gate before running — if should_run() is False
                    # the engine will return None (not a failure), don't escalate.
                    _ne_should, _ne_reason = self.ne.should_run()
                    if not _ne_should:
                        self.log.debug("NE_SKIP reason=%s", _ne_reason)
                        return
                    _ne_t0 = _t.monotonic()
                    result = self.ne.run(verbose=False)
                    self.log.info(
                        "NE_RUN elapsed_ms=%.0f", (_t.monotonic() - _ne_t0) * 1000.0
                    )
                    try:
                        self._cortex.record_metric(
                            "cognition.ne_cycle_result",
                            1.0 if result else 0.0,
                        )
                    except Exception as _m_e:
                        self.log.debug("NE_METRIC: %s", _m_e)
                    if result:
                        self._ne_stuck_count = 0
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
                                self.log.error("BARE_EXCEPT: %s", _bare_e)
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
                                self.log.error("PSYCH_LOG: %s", _psych_e)
                    else:
                        self._ne_stuck_count += 1
                        try:
                            from .escalate import escalate_to_channel as _esc

                            # Include recent scheduler tick results so the NE-empty
                            # message is diagnostic rather than opaque.
                            _sched_summary = ""
                            try:
                                _ticks = self._cortex.twm_read(limit=10)
                                _tick_lines = [
                                    r["content_csb"]
                                    for r in _ticks
                                    if r.get("content_csb", "").startswith(
                                        "SCHEDULER_TICK|"
                                    )
                                ]
                                if _tick_lines:
                                    # Format: "habit_id=result" pairs, last 3 ticks
                                    _parts = []
                                    for _line in _tick_lines[-3:]:
                                        _segs = _line.split("|", 2)
                                        if len(_segs) == 3:
                                            _parts.append(f"{_segs[1]}={_segs[2][:60]}")
                                    if _parts:
                                        _sched_summary = " last ticks: " + ", ".join(
                                            _parts
                                        )
                            except Exception:
                                pass

                            _esc(
                                f"[NE] cycle produced no result — Igor may be stuck. "
                                f"Last valence: {self._last_ne_valence:.2f}. "
                                "Nothing actionable in TWM — watch-question scan runs "
                                f"next lever-watcher cycle.{_sched_summary}",
                                dedup_key="ne-empty-result",
                            )
                        except Exception as _esc_e:
                            self.log.error("NE_ESCALATE: %s", _esc_e)
                        # SOAR-style impasse: after 3 no-result cycles write a
                        # self-diagnostic subgoal to TWM so the next cycle has
                        # actionable content rather than looping identically.
                        if self._ne_stuck_count == 3:
                            try:
                                self._cortex.twm_push(
                                    source="coa_impasse",
                                    content_csb=(
                                        f"IMPASSE|NE produced no result "
                                        f"{self._ne_stuck_count} consecutive cycles — "
                                        "self-diagnostic subgoal: what is blocking cognition?"
                                    ),
                                    salience=0.9,
                                    urgency=0.7,
                                    category="impasse",
                                    ttl_seconds=600,
                                )
                                self.log.info(
                                    "NE_IMPASSE stuck_count=%d — impasse subgoal pushed to TWM",
                                    self._ne_stuck_count,
                                )
                            except Exception as _imp_e:
                                self.log.error("NE_IMPASSE: %s", _imp_e)
                        # LIDA DMN analog: stuck for 5+ cycles → switch to synthesis
                        # mode rather than continuing to idle. Dreaming can surface
                        # new content and reset the cognitive state.
                        if self._ne_stuck_count >= 5:
                            try:
                                from .dreaming import run as _dreaming_run

                                _stuck_n = self._ne_stuck_count
                                self._ne_stuck_count = 0
                                _dreaming_run()
                                self.log.info(
                                    "NE_STUCK_DREAMING: dreaming pass triggered after "
                                    "%d consecutive no-result cycles",
                                    _stuck_n,
                                )
                            except Exception as _stuck_dream_e:
                                self.log.error("NE_STUCK_DREAMING: %s", _stuck_dream_e)
                except Exception as _bare_e:
                    self.log.error("BARE_EXCEPT: %s", _bare_e)
                # NE grader — fresh-context quality evaluation (T-igor-ne-grader-pass)
                try:
                    if result:
                        _grade_result = _grade_ne_output(result, self._last_ne_valence)
                        if _grade_result:
                            import json as _json
                            from ..paths import paths as _paths

                            _psych_log = _paths().logs / "igor_psych.jsonl"
                            _psych_log.parent.mkdir(parents=True, exist_ok=True)
                            with open(_psych_log, "a") as _f:
                                _f.write(_json.dumps(_grade_result) + "\n")
                            # Feed grader result back into cognition so NE can self-correct.
                            # Threshold: overall < 0.4 OR any dimension < 0.3.
                            try:
                                _dims = (
                                    "memory_retrieval_quality",
                                    "context_assembly_quality",
                                    "output_coherence",
                                )
                                _overall = _grade_result.get("overall_score", 1.0)
                                _failing = [
                                    d for d in _dims if _grade_result.get(d, 1.0) < 0.3
                                ]
                                if _overall < 0.4 or _failing:
                                    _alert_dim = _failing[0] if _failing else "overall"
                                    _alert_score = (
                                        _grade_result.get(_alert_dim, _overall)
                                        if _failing
                                        else _overall
                                    )
                                    self._cortex.twm_push(
                                        source="coa_ne_grader",
                                        content_csb=(
                                            f"NE_QUALITY_ALERT"
                                            f"|dim={_alert_dim}"
                                            f"|score={_alert_score:.2f}"
                                        ),
                                        salience=0.75,
                                        urgency=0.5,
                                        category="ne_quality",
                                        ttl_seconds=300,
                                    )
                                    self.log.info(
                                        "NE_QUALITY_ALERT dim=%s score=%.2f overall=%.2f",
                                        _alert_dim,
                                        _alert_score,
                                        _overall,
                                    )
                            except Exception as _qa_e:
                                self.log.error("NE_QUALITY_ALERT: %s", _qa_e)
                except Exception as _grade_e:
                    self.log.error("NE_GRADER: %s", _grade_e)
                # Annotate pending engrams (batch_size=2 to stay within budget)
                try:
                    from ..memory.purpose_annotator import (
                        annotate_pending as _annotate_pending,
                    )

                    _n_annotated = _annotate_pending(self._cortex, batch_size=2)
                    if _n_annotated > 0:
                        self.log.info(
                            "purpose_annotator: annotated %d engrams", _n_annotated
                        )
                except Exception as _ann_e:
                    self.log.error("PURPOSE_ANNOTATOR: %s", _ann_e)
                # Scan watch_problems for incoming levers (D-escalate-as-default-2026-05-10)
                try:
                    from .watch_problems import lever_watcher as _lever_watcher

                    _lever_watcher()
                except Exception as _lw_e:
                    self.log.error("LEVER_WATCHER: %s", _lw_e)
                # Dreaming: cross-session synthesis every IGOR_DREAMING_INTERVAL cycles
                try:
                    import os as _os

                    _dreaming_interval = int(_os.getenv("IGOR_DREAMING_INTERVAL", "50"))
                    if _dreaming_interval > 0:
                        self._ne_cycle_counter += 1
                        if self._ne_cycle_counter % _dreaming_interval == 0:
                            from .dreaming import run as _dreaming_run

                            _dreaming_run()
                except Exception as _dream_e:
                    self.log.error("DREAMING: %s", _dream_e)
                try:
                    _exp_sched = getattr(igor, "_experiment_scheduler", None)
                    if _exp_sched is not None:
                        _exp = _exp_sched.tick()
                        if _exp:
                            self.log.info(
                                "experiment_tick: ran %s → %s",
                                _exp.experiment_id,
                                _exp.status.value,
                            )
                except Exception as _exp_e:
                    self.log.error("EXPERIMENT_TICK: %s", _exp_e)

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
                self.log.error("SILENT_EXCEPT: %s", _exc)
        finally:
            self._ne_spawn_lock.release()


def _grade_ne_output(result: dict, last_valence: float) -> dict | None:
    """Fresh-context quality evaluation of one NE cycle output.

    Evaluates three sub-scores (0.0-1.0 each):
      memory_retrieval_quality — were memory_candidates plausibly grounded in the obs?
      context_assembly_quality — does the output show coherent context, not scattered noise?
      output_coherence         — clear cognitive focus vs. rambling or self-referential output?

    Returns a psych_log entry dict, or None on failure. Escalates if overall < 0.5
    or any sub-score < 0.3.

    T-igor-ne-grader-pass / D-dreaming-patterns-2026-05-10
    """
    try:
        from ..tools.inner_cc import call_inner_cc_long

        summary = (result.get("summary_csb") or "")[:300]
        candidates = result.get("memory_candidates") or []
        n_candidates = len(candidates)
        impulses = result.get("action_impulses") or []
        thread = result.get("thread_topic", "")

        prompt = f"""You are a quality auditor for an AI agent's reasoning cycle. Rate this cycle output.

Summary: {summary}
Thread topic: {thread}
Memory candidates generated: {n_candidates}
Action impulses: {len(impulses)}
Valence: {last_valence:.2f}

Score each dimension 0.0-1.0. Respond ONLY with valid JSON:
{{
  "memory_retrieval_quality": <0.0-1.0 — did the agent retrieve/generate grounded, relevant memories?>,
  "context_assembly_quality": <0.0-1.0 — was the context coherent, not scattered or noisy?>,
  "output_coherence": <0.0-1.0 — clear cognitive focus vs rambling or self-referential?>,
  "notes": "<one sentence>"
}}"""

        raw = call_inner_cc_long(task=prompt, model="anthropic/claude-haiku-4-5")
        answer = (raw.get("answer") or "").strip()
        if not answer:
            return None
        if answer.startswith("```"):
            answer = answer.split("```")[1]
            if answer.startswith("json"):
                answer = answer[4:]
        import json as _json

        scores = _json.loads(answer)
        mrq = float(scores.get("memory_retrieval_quality", 0.5))
        caq = float(scores.get("context_assembly_quality", 0.5))
        oc = float(scores.get("output_coherence", 0.5))
        overall = (mrq + caq + oc) / 3.0

        import time as _time

        entry = {
            "ts": _time.time(),
            "entry_type": "ne_grade",
            "memory_retrieval_quality": mrq,
            "context_assembly_quality": caq,
            "output_coherence": oc,
            "overall_score": overall,
            "grade_notes": scores.get("notes", ""),
        }

        if overall < 0.5 or mrq < 0.3 or caq < 0.3 or oc < 0.3:
            try:
                from .escalate import escalate_to_channel as _esc

                _esc(
                    f"[NE grader] low quality score: overall={overall:.2f} "
                    f"mem={mrq:.2f} ctx={caq:.2f} coh={oc:.2f} — {scores.get('notes','')}",
                    dedup_key="ne-grader-low-score",
                )
            except Exception as _esc_e:
                _coa_log.warning("NE_GRADER: _esc push failed: %s", _esc_e)

        return entry
    except Exception as _e:
        _coa_log.error("NE_GRADER: %s", _e)
        return None


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
