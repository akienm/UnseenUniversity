"""narrative_engine.py — Arc builder & coherence checker. Transforms TWM into context.

WHAT IT IS
──────────
The Narrative Engine (NE) is Igor's coherence subsystem. It runs in the
main loop, monitoring Temporal Working Memory (TWM) for unintegrated
observations. When ≥ 5 obs are pending or 5 minutes have elapsed, NE
fires to answer three questions:

  1. What is Igor experiencing right now?
  2. What does this mean for Igor's goals/state?
  3. What should Igor do?

Output: a compressed narrative arc (always deterministic) + LTM
promotions and action impulses (via LLM inference). NE is the
bridge between sensation and long-term consolidation — it fuses TWM +
ring memory + active goals + affective state into a reasoner-ready
context.

WHY IT EXISTS
─────────────
Without NE, raw observations would pile up in TWM with no integrated
meaning. NE is the synthesis layer: it detects what matters now, decides
what's important enough to remember, and generates action impulses. It
also houses sleep consolidation — during idle periods NE runs a Hebbian
wandering pass over recent search traces, discovering and strengthening
co-activation patterns (D353). NE closes the loop: sensation → synthesis
→ consolidation.

HOW IT WORKS (architecture)
───────────────────────────
Entry: run() — main NE cycle.
  1. Read + filter TWM (loop guard: drop NE's own output via dual-axis
     filter — source != "narrative_engine" AND content_csb does not start
     with "ACTION_IMPULSE|").
  2. Focus pass: compute co-activation across active slots (D099, D100).
  3. Build deterministic arc from top observations (zero inference).
  4. Call inference_gateway.call("ne", prompt, ctx) for LLM synthesis.
  5. Promote high-importance candidates to LTM via cortex.store().
  6. Queue action impulses to TWM (source="narrative_engine",
     category="impulse").
  7. Update traversal cursor (track which thread NE is following).
  8. Run deep consolidation pass (_deep_consolidation_pass) during idle
     windows — structural: TWM promote, cluster merge, link prune, orphan
     adopt. SleepConsolidation push source handles Hebbian binding
     discovery from search traces (D353).

Key subsystem relationships:
  Reads     — cortex.twm_read(), twm_get_slots(), get_portable()
  Writes    — cortex.store() for LTM; cortex.write_ring() for session notes
  Inference — inference_gateway.call("ne", …) routes per D211 / D234
  Milieu    — cortex.get_milieu() fetches affect for promotion weighting
              (D305)
  Slots     — co-activation logic fuses multiple action pointers
              (D099, D100)
  Threading — traversal cursor detects oscillation vs convergence

TWM → Arc fusion (the core transform)
─────────────────────────────────────
NE consumes TWM observations with:
  source, content_csb (≤ 200 chars), salience [0,1], urgency [0,1]
  (orthogonal to salience — D352 gating rule), metadata (action_pointer).

Focus pass (D099 + D100): sorts by (urgency × salience) + co-activation
bonus. Co-activation = how many TWM slots point to the same goal node.
Shared goals amplify relative salience. Solo observations decay at 0.7.

Deterministic arc (always built): top 6 observations deduplicated into a
1-2 sentence summary written to ring_memory category="narrative".

Inference path
──────────────
Builds a prompt from obs_text + last_narrative + cursor_context. Calls
inference_gateway.call("ne", prompt, ctx) with ctx.is_background=True.
Reasoning cache (D018): 12-min TTL + TWM watermark invalidation.

LLM response JSON (parsed):
  summary_csb, thread_topic, connections,
  salience_updates    [{obs_id, new_salience}],
  memory_candidates   [{content_csb, importance, memory_type, valence}],
  action_impulses     [{action, urgency, why}],
  internal_state      {valence, arousal, notes},
  narrative_gaps      [{question, salience, threat_level}]

Promotion: candidates with importance ≥ PROMOTE_THRESHOLD (0.7) are
wrapped in Memory objects with memory_type, parent_id (routed via
CP1-CP6), and emotional profile (amygdala analog, D305). Each promoted
memory carries provenance: ne_run count, encoding context, arousal-
weighted importance. If an obs is promoted, its TTL is extended
(persists longer in TWM).

Action impulses are queued to TWM with source="narrative_engine",
category="impulse". thalamus.process_input() later sees them and fires
habits or impulse_executor.

Traversal cursor — tracks which thread NE is following across cycles:
  topic_history — last N thread labels (from thread_topic output)
  depth         — cycles on current topic
  status        — active | converging | oscillating
  Oscillation (same topic + no new promotions for N cycles) → NE prompted
  to "seek a different thread."
  Convergence (high promotion rate) → "continue deepening."

Offline consolidation (_deep_consolidation_pass)
────────────────────────────────────────────────
Runs during deep idle windows (IGOR_CONSOLIDATION_IDLE_MIN, default 20
min). Does structural maintenance: TWM observation promotion, episodic
cluster merge, weak link pruning, orphan adoption, reading integration.
Does NOT do Hebbian learning — see SleepConsolidation push source below.

Hebbian consolidation (SleepConsolidation push source, D353)
─────────────────────────────────────────────────────────────
Runs during quiet periods (10+ min no user input, every 5 min). Reads
recent search traces, finds co-activated node pairs (nodes that fired
together in ≥ 2 searches), creates or strengthens missing edges via
Hebbian binding. NE quietly solidifies patterns discovered during waking
hours.

Output shape (with LLM enabled):
  { summary_csb, thread_topic, connections, salience_updates,
    memory_candidates, action_impulses, internal_state, narrative_gaps }

Output shape (LLM disabled):
  { arc, promoted: 0, impulses: [] }

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D018  reasoning cache — 12-min TTL + TWM watermark; NE output cached
  D099  TWM global workspace — Baars GWT; 7-slot ring with parent_obs_id
  D100  live-salience co-activation — salience computed live from slot
        node-count, not stored
  D186  affective NE arousal amplification — arousal weights gap salience
        + urgency
  D211  local-first inference — tier.2 Ollama primary; cloud fallback
  D234  tier-ladder redesign — graph > Ollama > OR; LLM calls train
        their replacement
  D243  deterministic arc — NE builds arc always; LLM output optional
  D252  calibre 8-tier arousal — encoding_arousal shapes reading priority
  D299  urgency gates — alerts (urgency ≥ 0.85) break through conversation
        gating
  D305  amygdala analog — NE uses milieu arousal to weight promotion
  D352  TWM attentional gating — conversation mode caps background
        salience; NE sees gated view
  D353  sleep consolidation — SleepConsolidation push source (Hebbian
        binding from traces) + _deep_consolidation_pass (structural)

ENGRAM PORTION (graph-side machinery)
─────────────────────────────────────
  PROC_NE_TRIGGER — habit that monitors TWM and fires run()
  PROC_SLEEP_CONSOLIDATION — habit that detects idle and spawns
                              consolidation
  Promoted memory nodes — carry metadata.provenance_source="ne_synthesis"
                           + validation_status="unvalidated"
  Co-activation structures — edges between goal nodes strengthened by
                              D100 logic
  Hot attractors — NE-seeded, densified by Hebbian consolidation (D353)

If you want to change:
  - When NE fires           — NE_TRIGGER_OBS, NE_MIN_INTERVAL_SEC,
                               NE_MAX_INTERVAL_SEC
  - Observation filtering   — _filter_obs(), _format_obs_csb()
                               (loop guards live here)
  - Promotion threshold     — PROMOTE_THRESHOLD (0.7)
  - Inference routing       — inference_gateway.py (this just calls it)
  - Affective weighting     — milieu.py + amygdala logic in promotion
  - Thread tracking         — TraversalCursor + _update_cursor()
  - Consolidation policy    — _deep_consolidation_pass() + idle detection
  - Arc generation          — _build_deterministic_arc() + ring write
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List

from . import reasoning_cache
from .forensic_logger import log_ne_run, cts as _cts, log_error

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType
from ..igor_base import IgorBase

# ── Config ─────────────────────────────────────────────────────────────────────
NE_MODEL = "ollama"  # reasoning cache key (stable; do not change)
NE_TRIGGER_OBS = 5  # Run if >= this many unintegrated obs
NE_MIN_INTERVAL_SEC = (
    5  # Minimum seconds between NE runs (was 30 — cursor makes fast cycles safe)
)
NE_MAX_INTERVAL_SEC = 300  # Maximum seconds between NE runs (5 min)
PROMOTE_THRESHOLD = 0.7  # importance >= this → goes to LTM
NE_CURSOR_HISTORY = 5  # How many cycles to keep in topic_history
NE_OSCILLATION_DEPTH = 3  # Cycles on same topic with no new promotions → oscillating
NARRATIVE_GAP_MAX_AGE_MINUTES = 60  # Auto-close gaps unresolved longer than this

# Invariant (D-stew-ne-salience): stew observations push at _STEW_SALIENCE_PUSH;
# NE force-runs when it sees salience >= _NE_FORCE_RUN_THRESHOLD.
# _STEW_SALIENCE_PUSH must stay above _NE_FORCE_RUN_THRESHOLD so stew content
# always qualifies for a force-run.  Guarded at NarrativeEngine.__init__.
_STEW_SALIENCE_PUSH = 0.65
_NE_FORCE_RUN_THRESHOLD = 0.6

# D228 step 2: prediction error training
_PE_HEAT_THRESHOLD = 0.3  # min spread heat to count as a predicted node
_PE_REINFORCE_DELTA = 0.05  # strengthen correct predictions by this amount
_PE_WEAKEN_DELTA = 0.02  # weaken wrong predictions by this amount

# WO7: NE loop prevention — comprehensive guards

# source_filter: sources whose TWM entries NE must never re-process
# (NE's own output chain — re-reading would cause recursive self-detection)
_NE_EXCLUDED_SOURCES = frozenset(
    {
        "narrative_engine",  # direct NE TWM pushes (action impulses, promoted echoes)
        "ne_loop_guard",  # reserved for any future loop-guard writes
    }
)

# content_filter: content prefixes that identify NE's own output echoing back
# through TWM (even if source field was overwritten or re-surfaced by other agents)
_NE_CONTENT_PREFIXES = (
    "ACTION_IMPULSE|",
    "IMPULSE_QUEUED|",
    "IMPULSE_EXECUTED|",
    "NE_DIAG|",
    "[NE#",
    "NE_OBS_CAPPED|",
    "NARRATIVE_GAP|",  # gap registry entries — managed by _process_gaps(), not synthesis
)

# diagnostic_filter: keywords that mark self-referential/operational noise
# (change.20a.2, expanded in WO7)
_SELF_DIAG_KEYWORDS = (
    "loop",
    "stall",
    "recursive",
    "detecting own",
    "consolidation",
    "narrative engine",
    "ne run",
    "ne_run",
    "action impulse",
    "action_impulse",
    "self-detect",
    "self_detect",
)

# ── Prospective prediction ─────────────────────────────────────────────────────


@dataclass
class ProspectivePrediction:
    """Result of a prospective NE pass — prediction made before a turn is processed."""

    predicted_habit_id: Optional[str]  # None = no habit predicted to fire
    confidence: float = 0.0  # 0.0–1.0
    pre_warmed_memory_ids: list = field(default_factory=list)
    # #50: NE as incremental predictive parser — predicted upcoming topics from word graph
    predicted_search_keys: list = field(default_factory=list)  # top co-occurring words


# ── Traversal Cursor (#236) ────────────────────────────────────────────────────


@dataclass
class TraversalCursor:
    """
    #236: Explicit thread tracker for NE across cycles.
    Lives on the NE instance (not per-cycle). Tracks what thread NE has been
    following over time. Oscillation = same topic N cycles with no new promotions.
    Status transitions: active → converging (high promotions) | oscillating (stuck).
    """

    thread_id: str = field(default_factory=lambda: datetime.now().strftime("%H%M%S"))
    topic_history: List[str] = field(
        default_factory=list
    )  # last NE_CURSOR_HISTORY topics
    depth: int = 0  # cycles on current thread
    status: str = "active"  # active | converging | oscillating
    last_updated: str = ""
    promotions_this_thread: int = 0  # total promotions since thread started


# ── Prompt token cap ───────────────────────────────────────────────────────────
# token_cap 2000 (WO7): cap observation block at 2000 tokens
# Rough estimate: 4 chars per token. Oldest observations are dropped first (FIFO).
NE_MAX_OBS_CHARS = 8000  # 2000 tokens × 4 chars/token


class NarrativeEngine(IgorBase):
    """
    Coherence-checker. Runs in the main loop. Stateless between runs —
    all state lives in TWM (Postgres).
    """

    def __init__(
        self,
        cortex: Cortex,
        instance_id: str = "wild-0001",
        stew_salience: float = _STEW_SALIENCE_PUSH,
        force_run_threshold: float = _NE_FORCE_RUN_THRESHOLD,
    ):
        super().__init__()
        if stew_salience <= force_run_threshold:
            msg = (
                f"NE invariant violated: stew_salience ({stew_salience}) must be "
                f"> force_run_threshold ({force_run_threshold}); stew observations "
                "would not qualify NE for a force-run."
            )
            self.log.error(msg)
            raise ValueError(msg)
        self.cortex = cortex
        self.instance_id = instance_id
        self._last_run: Optional[datetime] = None
        self._run_count: int = 0
        self._last_ne_model: str = NE_MODEL  # #84: updated to actual model on each run
        self._last_prediction: Optional[ProspectivePrediction] = None  # #121
        self._cursor: TraversalCursor = TraversalCursor()  # #236: thread tracker

    # ── Prospective pass (#121) ────────────────────────────────────────────────

    def prospective_pass(
        self,
        recent_obs: list[dict],
        habits: list,
        word_graph=None,
    ) -> ProspectivePrediction:
        """
        #121 + #50: Rule-based forward prediction — no LLM, must be fast (every turn).

        Looks at recent TWM observations and predicts:
          - which habit is likely to fire (#121)
          - which topics are likely upcoming via word graph co-occurrence (#50)

        The #50 predicted_search_keys pre-warm memory retrieval before the user
        input is fully parsed — NE starts pulling context the moment prior
        observations suggest a topic shift.

        Stores result in self._last_prediction for comparison after basal ganglia.
        """
        window = " ".join(o.get("content_csb", "") for o in recent_obs[-5:]).lower()

        best_habit = None
        best_score = 0.0
        for habit in habits:
            trigger = habit.metadata.get("trigger", "").lower()
            if not trigger or trigger not in window:
                continue
            # Score: base + recency boost from activation_count
            score = 0.5 + min(0.3, habit.activation_count * 0.01)
            if score > best_score:
                best_score = score
                best_habit = habit

        # #50: word graph predicts upcoming topics from the context window.
        # These become pre-warmed memory search keys — retrieved before preparse.
        _search_keys: list[str] = []
        if word_graph is not None:
            try:
                predictions = word_graph.predict_next(window, n=8)
                # Filter: skip short words, bigrams (contain "__"), and stop words
                _STOP = {"the", "and", "for", "that", "this", "with", "have", "from"}
                _search_keys = [
                    w
                    for w, _ in predictions
                    if len(w) > 3 and "__" not in w and w not in _STOP
                ][:3]
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )

        pred = ProspectivePrediction(
            predicted_habit_id=best_habit.id if best_habit else None,
            confidence=best_score,
            pre_warmed_memory_ids=[best_habit.id] if best_habit else [],
            predicted_search_keys=_search_keys,
        )
        self._last_prediction = pred
        return pred

    def record_actual(self, actual_habit_id: Optional[str]) -> None:
        """
        #121/#45: Compare actual fired habit to prospective prediction.
        Logs NE_SURPRISE ring entry, boosts TWM salience when surprised,
        and reinforces/weakens link weights (G11 learning loop).
        Called by main.py after basal_ganglia.select_habit() resolves.
        """
        pred = self._last_prediction
        self._last_prediction = None
        if pred is None:
            return

        predicted = pred.predicted_habit_id

        # No signal if neither predicted nor fired
        if predicted is None and actual_habit_id is None:
            return

        if predicted == actual_habit_id:
            delta = 0.0  # correct prediction — no surprise
        elif predicted is None:
            delta = 0.4  # didn't predict a habit but one fired
        elif actual_habit_id is None:
            delta = 0.25  # predicted a habit but nothing fired
        else:
            delta = 0.8  # wrong habit predicted

        # G11: get TWM seed IDs for link reinforcement (also used for salience boost below)
        seed_ids: list = []
        recent_obs: list = []
        try:
            recent_obs = self.cortex.twm_read(limit=5, include_integrated=False)
            seed_ids = [obs["id"] for obs in recent_obs if obs.get("id")]
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

        if delta < 0.1:
            # Correct prediction — reinforce links from predicted habit to co-active seeds
            if predicted and seed_ids:
                try:
                    self.cortex.reinforce_links(predicted, seed_ids, +0.05)
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                    )
            return

        self.cortex.write_ring(
            f"NE_SURPRISE|predicted={predicted}|actual={actual_habit_id}|delta={delta:.2f}",
            category="ne_prediction",
        )

        # G11: weaken links that led to wrong prediction; reinforce links toward actual habit
        if predicted and seed_ids:
            try:
                self.cortex.reinforce_links(predicted, seed_ids, -0.10)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )
        if actual_habit_id and seed_ids:
            try:
                self.cortex.reinforce_links(actual_habit_id, seed_ids, +0.05)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )

        # Boost salience on recent TWM context proportional to surprise magnitude
        if delta >= 0.4:
            try:
                for obs in recent_obs:
                    boosted = min(1.0, obs["salience"] + delta * 0.3)
                    self.cortex.twm_update_salience(obs["id"], boosted)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )

    # ── Trigger logic ──────────────────────────────────────────────────────────

    def should_run(self) -> tuple[bool, str]:
        """
        Returns (should_run, reason).
        Checks timing constraints and observation count.

        IMPORTANT: Don't force-run on max interval if observations are stale
        (only low-salience timer/surfacer obs). Only force-run if truly stuck.
        """
        now = datetime.now()

        # Hard minimum: don't run too frequently
        if self._last_run is not None:
            elapsed = (now - self._last_run).total_seconds()
            if elapsed < NE_MIN_INTERVAL_SEC:
                return False, f"too_soon({elapsed:.0f}s < {NE_MIN_INTERVAL_SEC}s)"

        # Count unintegrated observations
        unintegrated = self.cortex.twm_count_unintegrated()

        # Run if enough meaningful observations piled up
        if unintegrated >= NE_TRIGGER_OBS:
            return True, f"obs_threshold({unintegrated}>={NE_TRIGGER_OBS})"

        # Force run only if truly max interval exceeded AND we have any meaningful observations
        # (not just timer heartbeats or background surfacing)
        if (
            self._last_run is None
            or (now - self._last_run).total_seconds() >= NE_MAX_INTERVAL_SEC
        ):
            # WO7: use _filter_obs() — excludes NE-originated sources AND content prefixes
            raw = self.cortex.twm_read(limit=50, include_integrated=True)
            obs_list = self._filter_obs(raw)
            has_meaningful = any(
                o["source"] in ("user_input", "discord", "gmail")
                or o["salience"] >= _NE_FORCE_RUN_THRESHOLD
                for o in obs_list
            )
            if has_meaningful:
                return True, "max_interval_exceeded_with_content"
            return False, f"max_interval_quiet({unintegrated} obs, all stale)"

        return False, f"quiet({unintegrated} unintegrated)"

    # ── Main run ───────────────────────────────────────────────────────────────

    def run(self, verbose: bool = True) -> Optional[dict]:
        """
        Run the Narrative Engine. Returns the NE output dict, or None on failure.
        Side effects: marks TWM entries integrated, updates salience, promotes to LTM.
        """
        t0 = time.perf_counter()
        should, reason = self.should_run()
        if not should:
            return None

        # WO7: filter out NE's own output on all axes (source + content prefix)
        _all_raw = self.cortex.twm_read(limit=50, include_integrated=True)
        raw_obs = self._filter_obs(_all_raw)

        # D099 + D100: focus pass — decay solo slots, compute live co-activation scores
        # D099: slots with no shared action_pointer with any other slot → decay at 0.7
        # D100: count how many slots reference each action_pointer node → co-activation score
        #       most-referenced node IS most salient right now (computed not stored)
        _co_activation: dict[str, int] = {}
        try:
            slots = self.cortex.twm_get_slots()

            def _slot_actions(slot):
                ap = slot["metadata"].get("action_pointer", "")
                return (
                    set(filter(None, (n.strip() for n in ap.split(","))))
                    if ap
                    else set()
                )

            # D100: tally co-activation across all slots
            for slot in slots:
                for node in _slot_actions(slot):
                    _co_activation[node] = _co_activation.get(node, 0) + 1

            # D099: decay solo slots (no shared action_pointer with any peer)
            if len(slots) > 1:
                for slot in slots:
                    mine = _slot_actions(slot)
                    if not mine:
                        continue
                    shared = any(
                        mine & _slot_actions(other)
                        for other in slots
                        if other["id"] != slot["id"]
                    )
                    if not shared:
                        self.cortex.twm_decay_slot(slot["id"], factor=0.7)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

        # Mark filtered-out unintegrated obs as integrated so they stop counting
        # toward the trigger threshold. They've been seen — just not processable.
        _filtered_ids = [
            o["id"] for o in _all_raw if not o.get("integrated") and o not in raw_obs
        ]
        if _filtered_ids:
            self.cortex.twm_mark_integrated(_filtered_ids)

        # D100: sort by urgency * salience + co-activation bonus (computed not stored)
        # co-activation bonus: each hot node shared by N slots contributes N * 0.1 to sort weight
        def _sort_weight(o):
            base = o.get("urgency", 0.2) * o.get("salience", 0.5)
            ap = o.get("metadata", {}).get("action_pointer", "")
            bonus = sum(
                _co_activation.get(n.strip(), 0) * 0.1
                for n in ap.split(",")
                if n.strip()
            )
            return base + bonus

        obs_list = sorted(raw_obs, key=_sort_weight, reverse=True)

        if not obs_list:
            self._last_run = datetime.now()
            return None

        # Cap observation list to stay within prompt token budget (change.20a.3)
        obs_list, dropped = self._cap_observations(obs_list)
        if dropped > 0:
            self.cortex.write_ring(
                f"NE_OBS_CAPPED|dropped={dropped}|kept={len(obs_list)}",
                category="ne_diagnostic",
            )
            if verbose:
                print(
                    f"{_cts()}[NE] Dropped {dropped} oldest obs (prompt token cap, kept {len(obs_list)})"
                )

        if verbose:
            print(f"\n[NE] Running (reason={reason}, obs={len(obs_list)})...")

        # Build CSB prompt
        obs_text = self._format_obs_csb(obs_list)
        last_narrative = self._get_last_narrative()

        # Task boundary check (T-igor-ne-task-boundary): if focus displaced since last
        # NE run, treat as task start — clear episodic context for a fresh window.
        try:
            import time as _time
            from .focus_state import is_task_boundary as _is_task_boundary

            if _is_task_boundary(getattr(self, "_last_run_wall_ts", 0.0)):
                last_narrative = ""
        except Exception as _tb_e:
            log.warning("NE._run_turn: task_boundary check failed: %s", _tb_e)
        self._last_run_wall_ts = __import__("time").time()

        prompt = self._build_prompt(obs_text, last_narrative)

        # Watermark for cache invalidation — max obs id already in hand
        max_twm_id = max((o["id"] for o in obs_list), default=0)

        # D228 step 2: collect seed memory IDs from TWM before inference, for prediction error
        _pe_seed_ids: list = []
        _pe_predicted_heat: dict = {}
        if os.getenv("IGOR_PREDICTION_ERROR_ENABLED", "false").lower() == "true":
            try:
                _pe_seed_ids = [
                    obs["metadata"]["memory_id"]
                    for obs in obs_list
                    if obs.get("metadata", {}).get("memory_id")
                ]
                if _pe_seed_ids:
                    _pe_predicted_heat = self.cortex.spreading_activation(
                        _pe_seed_ids, depth=2
                    )
                    if _pe_predicted_heat:
                        self.cortex.set_heat_field(_pe_predicted_heat)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )

        # Always write deterministic arc — no inference needed, always current.
        _det_arc = self._build_deterministic_arc(obs_list)
        if _det_arc:
            _arc_thread_id = None
            _tc: dict = {}
            for _o in obs_list:
                _tid = _o.get("thread_id")
                if _tid:
                    _tc[_tid] = _tc.get(_tid, 0) + 1
            if _tc:
                _arc_thread_id = max(_tc, key=_tc.get)
            self.cortex.write_ring(
                f"[NE#{self._run_count + 1}] {_det_arc}",
                category="narrative",
                thread_id=_arc_thread_id,
            )
            self.log.info("[NE] arc: %s", _det_arc)

        # LLM path: LTM promotion + action impulses.
        promoted, impulses, _pe_promoted_ids = 0, [], []
        result = self._call_inference(prompt, max_twm_id)
        if result is None:
            if verbose:
                print("[NE] LLM call failed — arc written, skipping promotion.")
            try:
                from .forensic_logger import log_anomaly as _la

                _la(kind="NE_FAIL", detail="all_local_and_cloud_failed")
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )
        else:
            promoted, impulses, _pe_promoted_ids = self._apply_output(
                result, obs_list, verbose=verbose
            )

        # D228 step 2: prediction error → per-turn graph training
        if _pe_seed_ids and _pe_predicted_heat and _pe_promoted_ids:
            self._train_prediction_error(
                _pe_seed_ids, _pe_predicted_heat, _pe_promoted_ids
            )

        # #236: update traversal cursor after output is applied (knows actual promoted count)
        if result is not None:
            self._update_cursor(result, promoted)

        self._last_run = datetime.now()
        self._run_count += 1

        # Binding: detect coalitions from TWM-seeded heat field (T-binding phase 1)
        try:
            from .coalition import detect_coalitions as _detect_coalitions

            _bind_seeds = [
                o.get("metadata", {}).get("memory_id")
                for o in obs_list
                if o.get("metadata", {}).get("memory_id")
            ]
            if len(_bind_seeds) >= 2:
                _bind_heat = self.cortex.spreading_activation(_bind_seeds, depth=1)
                if _bind_heat:
                    self.cortex.set_heat_field(_bind_heat)
                _coalitions = _detect_coalitions(self.cortex, _bind_heat)
                if _coalitions:
                    _top = _coalitions[0]
                    self.cortex.write_ring(
                        f"COALITION|size={_top['size']}|weight={_top['weight']}"
                        f"|centroid={_top['centroid']}",
                        category="ne_diagnostic",
                    )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"narrative_engine.binding: {_bare_e}",
            )

        # G-NE1: episodic-to-semantic merge pass (runs every cycle; conservative defaults)
        _merges = self._consolidation_merge_pass()
        if _merges > 0:
            print(f"{_cts()}[NE] merge: {_merges} cluster(s) → semantic nodes")

        # #309: memory reconsolidation pass — update flagged memories under high arousal
        _reconsolidated = self._reconsolidation_pass()
        if _reconsolidated > 0:
            print(f"{_cts()}[NE] reconsolidated: {_reconsolidated} memory/ies updated")

        # T-sleep-memory-auditor: chain prior-version reading memories to newer
        # deposits that cover the same point. Gated on IGOR_MEMORY_AUDITOR_ENABLED
        # so a restart with the env var off keeps existing behavior identical.
        if os.getenv("IGOR_MEMORY_AUDITOR_ENABLED", "false").lower() in (
            "1",
            "true",
            "yes",
        ):
            _chained = self._memory_auditor_pass()
            if _chained > 0:
                print(f"{_cts()}[NE] auditor: chained {_chained} prior-version edge(s)")

        log_ne_run(
            obs_count=len(obs_list),
            integrated=len(obs_list),
            promoted=promoted,
            impulses=impulses,
            model=self._last_ne_model,  # #84: actual model, not stale constant
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

        return result

    # ── Output processing ──────────────────────────────────────────────────────

    def _apply_output(
        self, result: dict, obs_list: list[dict], verbose: bool = True
    ) -> tuple[int, int, list]:
        """Apply NE output: update salience, mark integrated, promote to LTM.
        Returns (promoted_count, impulse_count, promoted_ids) for forensic logging
        and prediction error training."""

        # 1. Update salience for any obs the NE re-scored
        for update in result.get("salience_updates", []):
            obs_id = update.get("obs_id")
            new_sal = update.get("new_salience")
            if obs_id is not None and new_sal is not None:
                self.cortex.twm_update_salience(obs_id, float(new_sal))

        # 2. Mark all obs as integrated
        all_ids = [o["id"] for o in obs_list]
        self.cortex.twm_mark_integrated(all_ids)

        # 3. Promote high-importance candidates to LTM
        # change.20a.2: self-diagnostic content → ring(ne_diagnostic), never LTM
        #
        # Step 2 (#305): affective frame selection.
        # effective_importance = causal_coherence * (1-w) + milieu_alignment * w
        # where w = 0.4 * arousal (high arousal → affect biases selection more).
        # Frames affectively congruent with current milieu need less causal weight to promote.
        _aff_milieu = __import__(
            "wild_igor.igor.cognition.milieu", fromlist=["get"]
        ).get()
        try:
            _aff_ms = _aff_milieu.get_state() if _aff_milieu else None
        except Exception as e:
            log_error(
                kind="TOOL_FAIL", detail=f"milieu state fetch failed: {e}"
            )  # non-fatal
            _aff_ms = None
        _aff_arousal = max(0.0, _aff_ms.arousal if _aff_ms else 0.0)
        _aff_valence = _aff_ms.valence if _aff_ms else 0.0

        promoted = 0
        promoted_ids: list[str] = (
            []
        )  # D228 step 2: collected for prediction error training
        _promoted_contents: list[str] = []  # for Step 3 gap closure scan
        for cand in result.get("memory_candidates", []):
            importance = float(cand.get("importance", 0.0))
            content = cand.get("content_csb", "")

            # Self-diagnostic content must not enter LTM — MemorySurfacer would
            # re-surface it and restart the detection loop (change.20a.2)
            if self._is_self_diagnostic(content):
                self.cortex.write_ring(
                    f"NE_DIAG|{content[:300]}",
                    category="ne_diagnostic",
                )
                continue

            # Step 2 (#305): blend causal importance with affective alignment
            _cand_valence = float(cand.get("valence", 0.0))
            _milieu_alignment = 1.0 - abs(_cand_valence - _aff_valence) / 2.0
            _affective_weight = 0.4 * _aff_arousal
            _effective_importance = (
                importance * (1.0 - _affective_weight)
                + _milieu_alignment * _affective_weight
            )

            if _effective_importance >= PROMOTE_THRESHOLD:
                mem_type_str = cand.get("memory_type", "episodic")
                try:
                    mem_type = MemoryType(mem_type_str)
                except ValueError:
                    mem_type = MemoryType.EPISODIC

                # Track source obs IDs for Signal A TTL extension
                source_obs_id = cand.get("source_obs_id")

                # #66 / #305: amygdala analog — use milieu already fetched for Step 2
                _ms = _aff_ms
                _arousal = _aff_arousal
                _valence_enc = _aff_valence
                _emotionally_charged = (
                    _effective_importance >= 0.85 and abs(_arousal) > 0.4
                )

                # CP parent routing: NE candidates are routed by memory_type/content
                # CP1=uncertainty/identity threat; CP2=pattern/learning; CP3=causality;
                # CP4=experience/tool-use; CP5=inner state; CP6=ethics
                _cp_parent = "CP3"  # default: "there's always a why"
                if mem_type.value == "PROCEDURAL":
                    _cp_parent = "CP2"  # patterns and how-to → "I look for patterns"
                elif mem_type.value == "INTERPRETIVE":
                    _cp_parent = "CP2"  # meaning/insight → pattern recognition
                elif mem_type.value in ("EXPERIENTIAL", "EPISODIC"):
                    _cp_parent = "CP4"  # experience → "I learn from doing"
                elif mem_type.value == "FACTUAL":
                    _cp_parent = "CP3"  # stable facts → causality context
                elif abs(float(cand.get("valence", 0.0))) > 0.6:
                    _cp_parent = "CP5"  # emotionally significant → inner state

                # #188: procedural candidates get a trigger so basal_ganglia can score them
                _meta: dict = {
                    "source": "narrative_engine",
                    "importance": importance,
                    "ne_run": self._run_count + 1,
                    "promoted_at": datetime.now().isoformat(),
                    **({"emotionally_charged": True} if _emotionally_charged else {}),
                    # T-memory-provenance: tag NE-synthesised memories as unvalidated
                    "provenance_source": "ne_synthesis",
                    "validation_status": "unvalidated",
                }
                if mem_type == MemoryType.PROCEDURAL and "trigger" not in _meta:
                    _STOP = {
                        "that",
                        "this",
                        "with",
                        "have",
                        "from",
                        "when",
                        "igor",
                        "will",
                        "akien",
                        "then",
                    }
                    _tw = [
                        w.lower().strip(".,?!()[]")
                        for w in content.split()
                        if len(w) > 3
                    ]
                    _trigger_words = [w for w in _tw if w not in _STOP][:5]
                    if _trigger_words:
                        _meta["trigger"] = " ".join(_trigger_words)

                mem = Memory(
                    narrative=content,
                    memory_type=mem_type,
                    parent_id=_cp_parent,
                    valence=float(cand.get("valence", 0.0)),
                    arousal=_arousal,
                    source="narrative_engine",  # G46: provenance
                    context_of_encoding=(  # G46: encoding context; #305 affective fit
                        f"ne_run={self._run_count + 1} importance={importance:.2f} "
                        f"effective={_effective_importance:.2f} arousal={_arousal:.2f}"
                    ),
                    metadata=_meta,
                )
                self.cortex.store(mem)
                _promoted_contents.append(content)
                promoted_ids.append(mem.id)
                promoted += 1

                # Signal A (Change 3): extend TTL of source TWM obs when importance >= 0.7
                # The observation was confirmed relevant enough to persist in LTM.
                if source_obs_id is not None:
                    self.cortex.twm_extend_ttl(
                        source_obs_id, reason=f"ne_promoted_importance={importance:.2f}"
                    )

        # 4. Write narrative fragment to ring_memory ONLY if we promoted or got action impulses
        # (don't spam ring with empty/stale narratives)
        # change.20a.2: if summary itself is self-diagnostic, use ne_diagnostic category
        summary = result.get("summary_csb", "")
        actions = result.get("action_impulses", [])

        # Infer active thread_id from TWM obs — most common non-None thread_id wins.
        # This tags the narrative so _build_session_context() can use it as a temporal anchor.
        _thread_counts: dict = {}
        for _o in obs_list:
            _tid = _o.get("thread_id")
            if _tid:
                _thread_counts[_tid] = _thread_counts.get(_tid, 0) + 1
        _narrative_thread_id = (
            max(_thread_counts, key=_thread_counts.get) if _thread_counts else None
        )

        if summary and (promoted > 0 or actions):
            if self._is_self_diagnostic(summary):
                self.cortex.write_ring(
                    f"NE_DIAG|[NE#{self._run_count + 1}] {summary[:300]}",
                    category="ne_diagnostic",
                )
            else:
                self.cortex.write_ring(
                    f"[NE#{self._run_count + 1}] {summary[:300]}",
                    category="narrative",
                    thread_id=_narrative_thread_id,
                )

        if summary:
            self.log.info("[NE] arc: %s", summary[:240])
        if verbose and promoted > 0:
            self.log.debug("[NE] promoted=%d to LTM", promoted)

        # 5. Push action impulses back into TWM so they can be acted on
        # Dedup: don't re-push an impulse whose action keywords already appear in a
        # recent IMPULSE_EXECUTED ring entry. Prevents the NE from re-deriving the
        # same impulse every run when stale context keeps mentioning the same topic.
        _recent_executed = " ".join(
            e.get("content", "")
            for e in self.cortex.search_ring_text("IMPULSE_EXECUTED", limit=10)
        ).lower()

        impulse_count = 0
        for impulse in result.get("action_impulses", []):
            imp_urgency = float(impulse.get("urgency", 0.3))
            action = impulse.get("action", "")
            why = impulse.get("why", "")
            if not action:
                continue
            # Dedup check: if >2 significant words from this action already appear
            # in recently-executed impulses, skip — it's already been handled.
            _action_words = [
                w
                for w in action.lower().split()
                if len(w) > 3
                and w
                not in {"igor", "will", "akien", "that", "this", "with", "from", "have"}
            ]
            _already_done = (
                len(_action_words) >= 2
                and sum(1 for w in _action_words if w in _recent_executed) >= 2
            )
            if _already_done:
                self.cortex.write_ring(
                    f"NE_IMPULSE_DEDUP|skipped duplicate: {action[:80]}",
                    category="ne_diagnostic",
                )
                continue
            self.cortex.twm_push(
                source="narrative_engine",
                content_csb=f"ACTION_IMPULSE|urgency={imp_urgency:.2f}|{action}|why:{why}",
                salience=imp_urgency,
                metadata={"type": "action_impulse", "action": action, "why": why},
                ttl_seconds=300,  # impulses expire in 5 min if unacted
                urgency=0.6,  # Change 4: NE action impulses — moderately urgent
            )
            impulse_count += 1

        # #304/#306: gap registry — accumulate tension, push new gaps, close resolved ones
        self._process_gaps(result, _promoted_contents)

        return promoted, impulse_count, promoted_ids

    # ── Prediction error training ──────────────────────────────────────────────

    def _train_prediction_error(
        self,
        seed_ids: list,
        predicted_heat: dict,
        promoted_ids: list,
    ) -> None:
        """D228 step 2: prediction error → per-turn graph training.

        Edges that predicted correctly (spreading_activation predicted hot AND
        the node was actually promoted to LTM) are strengthened via reinforce_links.
        Edges that missed (predicted hot but not promoted) are weakened.

        Gated by IGOR_PREDICTION_ERROR_ENABLED=true. Never raises.
        """
        try:
            promoted_set = set(promoted_ids)
            predicted_hot = {
                nid
                for nid, heat in predicted_heat.items()
                if heat >= _PE_HEAT_THRESHOLD
            }
            hits = predicted_hot & promoted_set
            misses = predicted_hot - promoted_set

            for seed_id in seed_ids:
                if hits:
                    self.cortex.reinforce_links(
                        seed_id, list(hits), _PE_REINFORCE_DELTA
                    )
                if misses:
                    self.cortex.reinforce_links(
                        seed_id, list(misses), -_PE_WEAKEN_DELTA
                    )

            logging.getLogger("forensic").debug(
                "[NE] prediction_error: seeds=%d predicted_hot=%d hits=%d misses=%d",
                len(seed_ids),
                len(predicted_hot),
                len(hits),
                len(misses),
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

    # ── LLM calls ─────────────────────────────────────────────────────────────

    def _call_inference(self, prompt: str, max_twm_id: int = 0) -> Optional[dict]:
        """
        Run NE inference via the inference gateway (ne purpose).
        Checks reasoning cache first. Gateway routes: cloud_mode → OR;
        local NE model set → Ollama → OR fallback.
        Returns parsed result dict or None.
        """
        cached = reasoning_cache.get(NE_MODEL, prompt, max_twm_id)
        if cached is not None:
            result = self._parse_ne_json(cached)
            if result is not None:
                print(f"{_cts()}[NE] cache hit (twm_id≤{max_twm_id})")
                return result

        try:
            from .inference_gateway import get_gateway as _gw, make_context as _mk_ctx

            _ctx = _mk_ctx(is_background=True)
            text = _gw().call("ne", prompt, _ctx)
            result = self._parse_ne_json(text)
            if result is not None:
                _via = "cloud" if _ctx.cloud_active else "local"
                print(f"{_cts()}[NE] {_via} ok")
                reasoning_cache.put(NE_MODEL, prompt, text, max_twm_id)
                self._last_ne_model = f"gateway/ne/{_via}"
                return result
            print(
                f"{_cts()}[NE] JSON parse failed — skipping cycle | raw={text[:150]!r}"
            )
            try:
                from .forensic_logger import log_anomaly as _la

                _la(kind="NE_FAIL", detail=f"json_parse_failed raw={text[:150]!r}")
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                )
        except Exception as e:
            print(f"{_cts()}[NE] inference failed: {e}")
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _filter_obs(self, obs_list: list[dict]) -> list[dict]:
        """
        WO7: dual-axis NE loop guard.
        source_filter: drop entries whose source is in _NE_EXCLUDED_SOURCES.
        content_filter: drop entries whose content starts with an _NE_CONTENT_PREFIXES marker.
        Both axes are required — source field can be absent or overwritten by re-surfacing.
        """
        return [
            o
            for o in obs_list
            if o.get("source") not in _NE_EXCLUDED_SOURCES
            and not any(
                o.get("content_csb", "").startswith(p) for p in _NE_CONTENT_PREFIXES
            )
        ]

    def _is_self_diagnostic(self, text: str) -> bool:
        """Return True if text contains NE operational/self-diagnostic keywords (WO7, change.20a.2)."""
        low = text.lower()
        return any(kw in low for kw in _SELF_DIAG_KEYWORDS)

    def _format_obs_line(self, o: dict) -> str:
        """Format one TWM observation as a CSB line (shared by _format_obs_csb and _cap_observations)."""
        ts = o["timestamp"][11:16]  # HH:MM only
        src = o["source"]
        sal = f"{o['salience']:.2f}"
        intg = "✓" if o["integrated"] else "·"
        csb = o["content_csb"][:200]
        return f"{intg} [{ts}] src={src} sal={sal} | {csb}"

    def _format_obs_csb(self, obs_list: list[dict]) -> str:
        """Format TWM observations as a compact CSB block for the LLM prompt."""
        return "\n".join(self._format_obs_line(o) for o in obs_list)

    def _cap_observations(self, obs_list: list[dict]) -> tuple[list[dict], int]:
        """
        Trim obs_list to fit within NE_MAX_OBS_CHARS (change.20a.3).
        Drops oldest observations first (FIFO — list is sorted oldest-first).
        Returns (capped_list, dropped_count).
        """
        total = 0
        kept_reversed: list[dict] = []
        for obs in reversed(obs_list):  # newest first
            line_len = len(self._format_obs_line(obs))
            if total + line_len > NE_MAX_OBS_CHARS:
                break
            kept_reversed.append(obs)
            total += line_len
        dropped = len(obs_list) - len(kept_reversed)
        return list(reversed(kept_reversed)), dropped

    def _build_deterministic_arc(self, obs_list: list) -> str:
        """
        Build a narrative arc from current state without any LLM call.
        Uses top observations by sort weight + milieu valence.
        Written every NE cycle — always current, zero inference cost.
        """
        if not obs_list:
            return ""
        # Extract content snippets from top observations.
        # T-input-echo-ne-arc: TWM observations can overlap — the same user
        # text gets pushed under multiple category prefixes (USER_INPUT,
        # RELATIONSHIP, thread.recent_user, etc.) and each ends up producing
        # a snippet that shares common substrings with its siblings. Without
        # dedup, the arc line reads like Igor stuttering.
        # Pull top 6 (not 3) to survive dedup, then keep 3 distinct.
        raw_snippets: list[str] = []
        for obs in obs_list[:6]:
            raw = obs.get("content_csb", "")
            if "|" in raw:
                raw = raw.split("|", 1)[-1]
            snippet = raw[:80].strip()
            if snippet:
                raw_snippets.append(snippet)

        # Dedup: drop any snippet that is a substring of a later (longer) one,
        # and drop any that is a substring of what we've already kept.
        # Tolerant to whitespace/newline noise by comparing on a normalized
        # single-space form; the original (pre-normalize) text goes into
        # the rendered arc so the human-visible form is preserved.
        def _norm(s: str) -> str:
            return " ".join(s.split())

        snippets: list[str] = []
        for cand in raw_snippets:
            cand_norm = _norm(cand)
            if not cand_norm:
                continue
            subsumed = False
            # Drop already-kept snippets if this new one subsumes them.
            snippets = [s for s in snippets if _norm(s) not in cand_norm]
            # Skip this candidate if any kept snippet subsumes it.
            for kept in snippets:
                if cand_norm in _norm(kept):
                    subsumed = True
                    break
            if not subsumed:
                snippets.append(cand)
            if len(snippets) >= 3:
                break
        if not snippets:
            return ""
        # Optionally enrich with milieu valence
        _valence_str = ""
        try:
            _mil = __import__("wild_igor.igor.cognition.milieu", fromlist=["get"]).get()
            _ms = _mil.get_state() if _mil else None
            if _ms:
                _v = _ms.valence
                _valence_str = (
                    " (positive)" if _v > 0.3 else " (negative)" if _v < -0.3 else ""
                )
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"narrative_engine.py:991: {_exc}")
        focus = "; ".join(snippets)
        return f"Igor is engaged with{_valence_str}: {focus}."

    def _get_last_narrative(self) -> str:
        """Fetch the last NE narrative fragment from ring_memory for continuity."""
        entries = self.cortex.read_ring_memory(limit=20, category="narrative")
        if entries:
            return entries[-1]["content"][:300]
        return "(none — first NE run)"

    def _watch_context(self) -> str:
        """Load WATCH_Q and WATCH_T nodes from clan.memories for context assembly.

        WATCH_Q nodes (universal questions — what does this mean) appear first.
        WATCH_T nodes (personal topics — what does this mean to ME) appear second.
        This ordering implements two-stage latent variable compression:
        broad semantic matching collapses to personal relevance (D-activate-primitive-2026-05-10).
        """
        try:
            import os
            import psycopg2

            pg_url = os.environ.get(
                "IGOR_HOME_DB_URL",
                "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
            )
            conn = psycopg2.connect(pg_url)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, narrative FROM clan.memories"
                    " WHERE id LIKE 'WATCH_Q%' ORDER BY id"
                )
                watch_q = cur.fetchall()
                cur.execute(
                    "SELECT id, narrative FROM clan.memories"
                    " WHERE id LIKE 'WATCH_T%' ORDER BY id"
                )
                watch_t = cur.fetchall()
            conn.close()
            if not watch_q and not watch_t:
                return ""
            lines = ["WATCH_Q (universal questions Igor asks about any observation):"]
            for wid, narrative in watch_q:
                lines.append(f"  {wid}: {narrative}")
            lines.append("WATCH_T (topics Igor personally tracks and cares about):")
            for wid, narrative in watch_t:
                lines.append(f"  {wid}: {narrative}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _playbook_context(self) -> str:
        """Load active PLAYBOOK entries as a token-capped context block."""
        try:
            from .playbook import playbook_context_block

            return playbook_context_block()
        except Exception:
            return ""

    def _build_prompt(self, obs_text: str, last_narrative: str) -> str:
        cursor_ctx = self._cursor_context()
        watch_ctx = self._watch_context()
        watch_block = f"\n{watch_ctx}\n" if watch_ctx else ""
        playbook_ctx = self._playbook_context()
        playbook_block = f"\n{playbook_ctx}\n" if playbook_ctx else ""
        return f"""You are the Narrative Engine for Igor, an AI agent. Your job: make sense of what Igor is experiencing.

IDENTITY GUARD: The subject of all observations is IGOR (not "Claude", not "the AI", not "the model").
Igor is the agent. Akien is the human. Always write from Igor's perspective.
"Igor said...", "Igor learned...", "Igor completed..." — never "Claude learned" or "the model did."

SELF-REF GUARD (WO7): Focus ONLY on external events, user interactions, and Igor's goals.
Do NOT generate content describing your own NE process, loops, recursion, or self-observation.
Do NOT produce action_impulses about the NE itself, its loop detection, or its own operation.

LAST NARRATIVE:
{last_narrative}

TRAVERSAL STATE (#236):
{cursor_ctx}
{watch_block}{playbook_block}
CURRENT TWM OBSERVATIONS (✓=integrated, ·=new):
{obs_text}

Answer these three questions, then produce structured output:
1. What is Igor experiencing right now?
2. What does this mean for Igor's goals/state?
3. What (if anything) should Igor do?

Reply with ONLY a JSON object — no other text:
{{
  "summary_csb": "<50-100 word dense summary of Igor's current state and what it means>",
  "thread_topic": "<3-8 word label for the thread you are following this cycle — e.g. 'language learning queue drain'>",
  "connections": ["<observation pattern or link noticed>"],
  "salience_updates": [{{"obs_id": <int>, "new_salience": <0.0-1.0>}}],
  "memory_candidates": [
    {{
      "content_csb": "<key points only — what Igor did/learned, what it means; NOT verbatim; max 2 sentences; subject=Igor>",
      "importance": <0.0-1.0>,
      "memory_type": "<choose: episodic=one-time event Igor experienced; interpretive=meaning/insight Igor gained; procedural=pattern or method Igor uses; factual=stable reference fact>",
      "valence": <-1.0 to 1.0>
    }}
  ],
  "action_impulses": [{{"action": "<what Igor should do>", "urgency": <0.0-1.0>, "why": "<reason>"}}],
  "internal_state": {{"valence": <-1.0 to 1.0>, "arousal": <0.0-1.0>, "notes": "<brief>"}},
  "narrative_gaps": [
    {{"question": "<causal unknown — max 15 words; e.g. 'why did X happen after Y'>", "salience": <0.0-1.0>, "threat_level": <0.0-1.0>}}
  ]
}}
NARRATIVE_GAPS: list genuine causal unknowns that matter for predicting what happens next. Omit entry (empty list) if none."""

    # ── Traversal cursor (#236) ────────────────────────────────────────────────

    def _cursor_context(self) -> str:
        """
        #236: Format cursor state for injection into the NE prompt.
        When oscillating, adds a directive to seek a new thread.
        """
        c = self._cursor
        history = " → ".join(c.topic_history[-NE_CURSOR_HISTORY:]) or "(none)"
        lines = [
            f"thread_id={c.thread_id} depth={c.depth} status={c.status}",
            f"recent_threads: {history}",
        ]
        if c.status == "oscillating":
            lines.append(
                "OSCILLATION DETECTED: you have followed this thread without new insights "
                f"for {NE_OSCILLATION_DEPTH}+ cycles. Seek a different thread this cycle."
            )
        elif c.status == "converging":
            lines.append("Thread converging well — continue deepening or consolidate.")
        return "\n".join(lines)

    def _update_cursor(self, result: dict, promoted: int) -> None:
        """
        #236: Update traversal cursor after each NE cycle.
        - Reads thread_topic from NE output
        - Detects oscillation: same topic N cycles with no new promotions
        - Detects convergence: high promotion rate
        - Persists cursor snapshot to ring_memory (category=ne_cursor)
        """
        c = self._cursor
        topic = result.get("thread_topic", "").strip() or "(unlabelled)"

        # Append to history (cap at NE_CURSOR_HISTORY)
        c.topic_history.append(topic)
        if len(c.topic_history) > NE_CURSOR_HISTORY:
            c.topic_history = c.topic_history[-NE_CURSOR_HISTORY:]

        c.depth += 1
        c.promotions_this_thread += promoted
        c.last_updated = datetime.now().isoformat()

        # Oscillation detection: last N topics identical + no new promotions in this cycle
        recent = c.topic_history[-NE_OSCILLATION_DEPTH:]
        all_same = len(recent) >= NE_OSCILLATION_DEPTH and len(set(recent)) == 1
        if all_same and promoted == 0:
            c.status = "oscillating"
        elif promoted >= 2:
            c.status = "converging"
        else:
            c.status = "active"

        # When oscillating, reset thread so next cycle starts fresh
        if c.status == "oscillating":
            c.thread_id = datetime.now().strftime("%H%M%S")
            c.depth = 0
            c.promotions_this_thread = 0

        # Persist cursor snapshot to ring_memory for observability
        try:
            snapshot = (
                f"NE_CURSOR|thread={c.thread_id}|depth={c.depth}"
                f"|status={c.status}|topic={topic[:60]}"
                f"|promoted={promoted}"
            )
            self.cortex.write_ring(snapshot, category="ne_cursor")
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

    def _parse_ne_json(self, text: str) -> Optional[dict]:
        """Extract and parse JSON from LLM response. Returns None if unparseable."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return None

    # ── Affective narrative engine — Step 1: gap registry (#304) ───────────────

    def _process_gaps(
        self, result: dict, promoted_contents: list[str] | None = None
    ) -> int:
        """
        #304/#306 — Gap registry in TWM.

        1. Accumulate tension on existing unresolved gaps: salience rises each cycle
           proportional to milieu arousal (tension ~ unresolved_time * arousal).
        2. Close gaps whose question keywords overlap with just-promoted memory content:
           fire milieu dopamine signal (valence+arousal spike), mark gap integrated.
        3. Push new gaps detected by NE into TWM as NARRATIVE_GAP| entries.

        Gap entries are excluded from NE synthesis (_NE_CONTENT_PREFIXES) so they
        never feed back into the observation stream. They float up the TWM queue
        as salience rises, becoming visible to habits and action impulse dispatch.

        Returns count of new gaps pushed this cycle.
        """
        # Get milieu arousal to modulate tension accumulation rate
        _arousal = 0.3  # default if milieu unavailable
        try:
            _milieu_mod = __import__(
                "wild_igor.igor.cognition.milieu", fromlist=["get"]
            ).get()
            _ms = _milieu_mod.get_state() if _milieu_mod else None
            if _ms:
                _arousal = max(0.1, _ms.arousal)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"narrative_engine._process_gaps milieu: {_bare_e}",
            )

        # Read existing gap entries from active TWM
        existing_gaps: list[dict] = []
        try:
            all_obs = self.cortex.twm_read(limit=100, include_integrated=False)
            existing_gaps = [
                o
                for o in all_obs
                if o.get("content_csb", "").startswith("NARRATIVE_GAP|")
            ]
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"narrative_engine._process_gaps read: {_bare_e}",
            )

        # Accumulate tension: each unresolved cycle bumps salience by 5% × arousal.
        # If a gap has been open longer than NARRATIVE_GAP_MAX_AGE_MINUTES, auto-close it.
        _now = datetime.now()
        _max_age_sec = NARRATIVE_GAP_MAX_AGE_MINUTES * 60
        for gap_obs in existing_gaps:
            # Check age via first_pushed_at in metadata
            meta = gap_obs.get("metadata", {})
            _first_pushed = meta.get("first_pushed_at")
            if _first_pushed:
                try:
                    _age_sec = (
                        _now - datetime.fromisoformat(_first_pushed)
                    ).total_seconds()
                    if _age_sec > _max_age_sec:
                        # Auto-close: gap unresolved past max age
                        content = gap_obs.get("content_csb", "")
                        q_part = ""
                        for part in content.split("|"):
                            if part.startswith("question="):
                                q_part = part[9:]
                                break
                        age_min = int(_age_sec // 60)
                        try:
                            self.cortex.twm_mark_integrated([gap_obs["id"]])
                            self.cortex.write_ring(
                                f"NARRATIVE_GAP_TIMEDOUT|q={q_part[:80]}|age_min={age_min}",
                                category="ne_diagnostic",
                            )
                        except Exception as _bare_e:
                            log_error(
                                kind="BARE_EXCEPT",
                                detail=f"narrative_engine._process_gaps timeout: {_bare_e}",
                            )
                        continue  # Skip tension accumulation for closed gap
                except (ValueError, TypeError) as _exc:
                    from .forensic_logger import log_error as _le

                    _le(
                        kind="SILENT_EXCEPT", detail=f"narrative_engine.py:1216: {_exc}"
                    )

            current_sal = gap_obs.get("salience", 0.3)
            new_sal = min(1.0, current_sal + 0.05 * _arousal)
            try:
                self.cortex.twm_update_salience(gap_obs["id"], new_sal)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._process_gaps tension: {_bare_e}",
                )

        # Step 3 (#306): gap closure → dopamine signal
        # For each existing gap: if ≥2 significant keywords overlap with any just-promoted
        # memory, the gap is resolved — fire milieu valence+arousal spike, mark integrated.
        if promoted_contents:
            _promo_words = set()
            for pc in promoted_contents:
                _promo_words.update(w.lower() for w in pc.split() if len(w) > 3)

            for gap_obs in existing_gaps:
                content = gap_obs.get("content_csb", "")
                q_part = ""
                for part in content.split("|"):
                    if part.startswith("question="):
                        q_part = part[9:]
                        break
                if not q_part:
                    continue
                q_words = {w.lower() for w in q_part.split() if len(w) > 3}
                if len(q_words & _promo_words) >= 2:
                    # Gap resolved — fire dopamine-analog milieu signal
                    gap_tension = gap_obs.get("salience", 0.3)
                    _closure_valence = min(1.0, 0.3 + gap_tension * 0.4)
                    _closure_arousal = min(1.0, 0.4 + gap_tension * 0.3)
                    try:
                        _m = __import__(
                            "wild_igor.igor.cognition.milieu", fromlist=["get"]
                        ).get()
                        if _m:
                            _m.ingest_ne_state(
                                {
                                    "valence": _closure_valence,
                                    "arousal": _closure_arousal,
                                }
                            )
                    except Exception as _bare_e:
                        log_error(
                            kind="BARE_EXCEPT",
                            detail=f"narrative_engine._process_gaps closure milieu: {_bare_e}",
                        )
                    try:
                        self.cortex.twm_mark_integrated([gap_obs["id"]])
                        self.cortex.write_ring(
                            f"NARRATIVE_GAP_CLOSED|q={q_part[:80]}|tension={gap_tension:.2f}",
                            category="ne_diagnostic",
                        )
                    except Exception as _bare_e:
                        log_error(
                            kind="BARE_EXCEPT",
                            detail=f"narrative_engine._process_gaps close: {_bare_e}",
                        )
                    # D277: dopamine → word graph weight update (close the learning loop).
                    # Gap resolved = training signal. Strengthen word paths that led here.
                    try:
                        _wg = getattr(self.cortex, "word_graph", None)
                        if _wg is not None and q_part:
                            _wg.reinforce_text(q_part, boost=0.05)
                    except Exception as _bare_e:
                        log_error(
                            kind="BARE_EXCEPT",
                            detail=f"narrative_engine gap closure wg reinforce: {_bare_e}",
                        )

        new_gaps = result.get("narrative_gaps", [])
        if not new_gaps:
            return 0

        # Build keyword sets from existing gaps for dedup (re-read: some may be closed now)
        existing_keywords: list[set] = []
        for gap_obs in existing_gaps:
            content = gap_obs.get("content_csb", "")
            q_part = ""
            for part in content.split("|"):
                if part.startswith("question="):
                    q_part = part[9:]
                    break
            if q_part:
                existing_keywords.append(
                    {w.lower() for w in q_part.split() if len(w) > 3}
                )

        pushed = 0
        for gap in new_gaps:
            question = gap.get("question", "").strip()
            salience = float(gap.get("salience", 0.3))
            threat = float(gap.get("threat_level", 0.1))
            if not question:
                continue

            # Dedup: skip if ≥2 significant words overlap with any existing gap
            q_words = {w.lower() for w in question.split() if len(w) > 3}
            is_duplicate = any(len(q_words & ex_kw) >= 2 for ex_kw in existing_keywords)
            if is_duplicate:
                continue

            # Step 4 (#307): arousal amplifies initial gap salience
            # High arousal → gaps feel more urgent; sympathetic state confirms threat
            arousal_boost = _arousal * 0.2
            effective_salience = min(1.0, salience + arousal_boost)

            content_csb = (
                f"NARRATIVE_GAP|question={question[:120]}"
                f"|salience={effective_salience:.2f}|threat={threat:.2f}"
            )
            try:
                self.cortex.twm_push(
                    source="narrative_engine",
                    content_csb=content_csb,
                    salience=effective_salience,
                    metadata={
                        "type": "narrative_gap",
                        "threat_level": threat,
                        "first_pushed_at": datetime.now().isoformat(),
                    },
                    ttl_seconds=600,  # 10 min; resolved gaps decay naturally
                    urgency=0.2 + threat * 0.4 + _arousal * 0.1,
                )
                existing_keywords.append(q_words)  # prevent same-batch duplicates
                pushed += 1
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._process_gaps push: {_bare_e}",
                )

        return pushed

    # ── #309: Memory reconsolidation pass ──────────────────────────────────────

    def _reconsolidation_pass(self) -> int:
        """
        #309: Memory reconsolidation — retrieve flagged memories, compare against
        current understanding, update if new context extends or contradicts stored content.

        Runs each NE cycle. Only processes memories with reconsolidate_pending=True.
        Uses a minimal LLM call (one memory at a time, capped at IGOR_RECONSOLIDATION_MAX
        per cycle, default 2) to avoid cost blowup.

        Returns count of memories actually updated.
        """
        import os as _os

        if _os.getenv("IGOR_RECONSOLIDATION_ENABLED", "true").lower() == "false":
            return 0

        max_per_cycle = int(_os.getenv("IGOR_RECONSOLIDATION_MAX", "2"))

        # Fetch pending memories directly from DB
        try:
            from ..memory.db_proxy import PGDatabaseProxy as _PGProxy

            _is_pg = isinstance(self.cortex._db, _PGProxy)
            _pending_sql = (
                "SELECT id FROM memories WHERE jsonb_exists(metadata, 'reconsolidate_pending') "
                "ORDER BY activation_count DESC LIMIT %s"
                if _is_pg
                else "SELECT id FROM memories WHERE metadata LIKE '%\"reconsolidate_pending\"%' "
                "ORDER BY activation_count DESC LIMIT %s"
            )
            with self.cortex._conn() as conn:
                rows = conn.execute(_pending_sql, (max_per_cycle,)).fetchall()
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"narrative_engine._reconsolidation_pass fetch: {_bare_e}",
            )
            return 0

        if not rows:
            return 0

        # Get current milieu for context
        try:
            _milieu_mod = __import__(
                "wild_igor.igor.cognition.milieu", fromlist=["get"]
            ).get()
            _ms = _milieu_mod.get_state() if _milieu_mod else None
            _arousal = max(0.0, _ms.arousal) if _ms else 0.0
        except Exception as e:
            log_error(
                kind="TOOL_FAIL", detail=f"arousal fetch failed: {e}"
            )  # non-fatal
            _arousal = 0.0

        # Bail if arousal has dropped — reconsolidation window closed
        if _arousal < 0.3:
            return 0

        # Get recent TWM as context for comparison
        _twm_ctx = " | ".join(
            o.get("content_csb", "")[:80] for o in self.cortex.twm_read(limit=8)
        )

        updated = 0
        for row in rows:
            mem = self.cortex.get(row["id"])
            if mem is None or not mem.metadata.get("reconsolidate_pending"):
                continue

            # Build a targeted reconsolidation prompt
            prompt = (
                "You are reviewing an existing memory for update under emotional arousal.\n"
                f"STORED MEMORY: {mem.narrative[:400]}\n"
                f"CURRENT CONTEXT (recent observations): {_twm_ctx[:600]}\n\n"
                "Does the current context extend, correct, or confirm this memory? "
                'Reply in JSON: {"action": "update"|"confirm"|"skip", '
                '"updated_narrative": "...", "importance_delta": -0.05..0.05, "reason": "..."}\n'
                "If action=confirm or skip, updated_narrative may be empty. Keep narratives concise."
            )

            try:
                from .inference_gateway import (
                    get_gateway as _gw,
                    make_context as _mk_ctx,
                )
                import json as _json

                _ctx = _mk_ctx(is_background=True)
                raw = _gw().call("ne", prompt, _ctx)
                parsed = None
                try:
                    _start = raw.find("{")
                    _end = raw.rfind("}") + 1
                    if _start >= 0 and _end > _start:
                        parsed = _json.loads(raw[_start:_end])
                except Exception as _e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/narrative_engine.py reconsolidate JSON parse: {_e}",
                    )

                if parsed is None or parsed.get("action") in (None, "skip", "confirm"):
                    # Just clear the flag — confirmed or unreadable
                    mem.metadata.pop("reconsolidate_pending", None)
                    mem.metadata.pop("reconsolidate_context", None)
                    mem.metadata.pop("reconsolidate_arousal", None)
                    self.cortex.store(mem)
                    continue

                if parsed.get("action") == "update":
                    new_narrative = (parsed.get("updated_narrative") or "").strip()
                    if new_narrative and len(new_narrative) > 20:
                        mem.narrative = new_narrative[:800]
                        delta = float(parsed.get("importance_delta", 0.0))
                        # Adjust activation_count as importance proxy (bounded)
                        mem.activation_count = max(
                            0, mem.activation_count + round(delta * 10)
                        )
                        mem.metadata["reconsolidated_at"] = datetime.now().isoformat()
                        mem.metadata["reconsolidated_reason"] = (
                            parsed.get("reason") or ""
                        )[:200]
                        mem.metadata.pop("reconsolidate_pending", None)
                        mem.metadata.pop("reconsolidate_context", None)
                        mem.metadata.pop("reconsolidate_arousal", None)
                        self.cortex.store(mem)
                        updated += 1
                        print(
                            f"{_cts()}[NE] reconsolidate: {mem.id} updated "
                            f"(arousal={_arousal:.2f}) reason={parsed.get('reason','')[:60]}"
                        )
                    else:
                        # Narrative too short or empty — clear flag only
                        mem.metadata.pop("reconsolidate_pending", None)
                        self.cortex.store(mem)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._reconsolidation_pass update {mem.id}: {_bare_e}",
                )
                # Clear flag to avoid retry loop on broken memories
                try:
                    mem.metadata.pop("reconsolidate_pending", None)
                    self.cortex.store(mem)
                except Exception as _e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/narrative_engine.py reconsolidate store: {_e}",
                    )

        return updated

    # ── G-NE1: Episodic-to-semantic merge pass ─────────────────────────────────

    def _consolidation_merge_pass(self) -> int:
        """
        G-NE1: After promotion pass, scan recent EPISODIC LTM nodes (source=narrative_engine)
        for cosine clusters. Clusters >= IGOR_NE_MERGE_MIN_CLUSTER get synthesized into one
        semantic node with metadata.occurrence_dates preserving all source timestamps.
        Returns number of merges performed.
        """
        import os as _os

        threshold = float(_os.getenv("IGOR_NE_MERGE_THRESHOLD", "0.85"))
        min_cluster = int(_os.getenv("IGOR_NE_MERGE_MIN_CLUSTER", "3"))
        window = int(_os.getenv("IGOR_NE_MERGE_WINDOW", "10"))

        # 1. Fetch recent EPISODIC memories promoted by this NE instance (T-no-row-scans: SQL filter)
        min_run = max(0, self._run_count - window)
        try:
            # SQL filters by source; remaining filters done in Python on smaller result set
            all_ne_episodics = self.cortex.get_by_type_and_source(
                MemoryType.EPISODIC, "narrative_engine", limit=500
            )
            candidates = [
                m
                for m in all_ne_episodics
                if m.metadata.get("ne_run", 0) >= min_run
                and not m.metadata.get("merged")  # skip already-merged nodes
            ]
        except Exception:
            return 0

        if len(candidates) < min_cluster:
            return 0

        # 2. Get stored embeddings (batch) + compute missing ones
        try:
            from ..cognition.embedder import embed as _embed, cosine_similarity as _cos
        except Exception:
            return 0  # embedder unavailable — skip silently

        ids = [m.id for m in candidates]
        emb_map = self.cortex._get_embeddings_batch(ids)
        vec_map: dict = {}
        for m in candidates:
            vec = emb_map.get(m.id)
            if vec is None:
                try:
                    vec = _embed(m.narrative)
                except Exception:
                    vec = None
            if vec is not None:
                vec_map[m.id] = vec

        # 3. Greedy cosine clustering
        merged_ids: set = set()
        clusters: list = []
        for i, m_i in enumerate(candidates):
            if m_i.id in merged_ids or m_i.id not in vec_map:
                continue
            cluster = [m_i]
            for m_j in candidates[i + 1 :]:
                if m_j.id in merged_ids or m_j.id not in vec_map:
                    continue
                if _cos(vec_map[m_i.id], vec_map[m_j.id]) >= threshold:
                    cluster.append(m_j)
            if len(cluster) >= min_cluster:
                clusters.append(cluster)
                for m in cluster:
                    merged_ids.add(m.id)

        if not clusters:
            return 0

        # 4. Merge each cluster
        merged = 0
        for cluster in clusters:
            try:
                self._merge_cluster(cluster)
                merged += 1
            except Exception as e:
                try:
                    from .forensic_logger import log_anomaly as _la

                    _la(kind="NE_MERGE_FAIL", detail=str(e)[:200])
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
                    )
        return merged

    def _merge_cluster(self, cluster: list) -> None:
        """
        G-NE1: Synthesize a cluster of similar EPISODIC memories into one node.
        Writes merged node with occurrence_dates; deletes originals.
        """
        narratives = [m.narrative for m in cluster]
        occurrence_dates = [
            m.metadata.get("promoted_at", m.timestamp.isoformat()) for m in cluster
        ]
        total_activation = sum(m.activation_count or 0 for m in cluster)

        # LLM synthesis: extract pattern, not individual events
        episodes_block = "\n".join(f"- {n}" for n in narratives)
        prompt = (
            "You are synthesizing similar episodic memories into a single semantic memory.\n\n"
            f"Episodes:\n{episodes_block}\n\n"
            'Respond with ONLY valid JSON: {"merged_narrative": "..."}\n\n'
            "The merged_narrative captures the recurring PATTERN across these episodes "
            'in 1-2 sentences. Do not mention specific dates or "multiple times".'
        )

        merged_narrative = max(narratives, key=len)  # safe fallback
        try:
            from .inference_gateway import get_gateway as _gw, make_context as _mk_ctx

            _ctx = _mk_ctx(is_background=True)
            raw = _gw().call("ne", prompt, _ctx)
            parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            merged_narrative = parsed.get("merged_narrative", merged_narrative)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

        mem = Memory(
            narrative=merged_narrative,
            memory_type=MemoryType.EPISODIC,
            parent_id="CP4",
            activation_count=total_activation,
            metadata={
                "source": "narrative_engine",
                "merged": True,
                "occurrence_dates": occurrence_dates,
                "merged_from_count": len(cluster),
                "merged_at": datetime.now().isoformat(),
                "ne_run": self._run_count,
            },
        )
        self.cortex.store(mem)

        for m in cluster:
            self.cortex.delete_memory(m.id)

        try:
            from .forensic_logger import log_anomaly as _la

            _la(
                kind="NE_MERGE",
                detail=(
                    f"merged {len(cluster)} EPISODIC → 1 semantic node; "
                    f"occurrence_dates={occurrence_dates}"
                ),
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/narrative_engine.py: {_bare_e}",
            )

    # ── T-sleep-memory-auditor: prior-version chaining for reading memories ───

    # Auditor config. Conservative defaults per Akien's 'start conservative'
    # guidance; tunable via env vars.
    _AUDITOR_COSINE_THRESHOLD = 0.85
    _AUDITOR_BATCH_WINDOW = 100  # recent reading memories to scan per pass
    _AUDITOR_MAX_CHAINS_PER_PASS = 10  # cap work so a pass never runs long

    @staticmethod
    def _provenance_completeness(meta: dict) -> float:
        """Fraction of full-provenance fields present on a memory.

        Five signals: book_title, source_author, chunk_position, model_used,
        inference_tier. Each contributes 0.2 when present+non-empty. An orphan
        reading memory (the 9,436-node population) scores ~0.0-0.2. A current-
        pipeline deposit scores 1.0. The auditor uses this as part of head
        selection — newer memories win, UNLESS their provenance is worse than
        an older one's.
        """
        if not meta:
            return 0.0
        score = 0.0
        for key in (
            "book_title",
            "source_author",
            "chunk_position",
            "model_used",
            "inference_tier",
        ):
            v = meta.get(key)
            if v is not None and str(v).strip() not in ("", "0"):
                score += 0.2
        return score

    @staticmethod
    def _audit_rank(confidence: float, provenance: float, age_seconds: float) -> float:
        """Rank memories for head selection: higher = better.

        confidence     — node's extraction_confidence (or 0.5 default)
        provenance     — 0..1 from _provenance_completeness
        age_seconds    — how old the memory is; newer is better but with a
                         sub-linear decay so a pristine old memory can still
                         beat a half-provenanced new one.
        """
        # Recency weight: exponential decay with 30-day half-life-ish.
        # ~1.0 at age 0, 0.37 at 30 days, 0.14 at 60 days, 0.05 at 90 days.
        # Prevents an ancient fully-provenanced memory from beating a
        # fresh-but-half-provenanced one on recency alone.
        import math as _math

        recency = _math.exp(-max(age_seconds, 0) / (86400 * 30))
        return confidence * (0.5 + 0.5 * provenance) * recency

    def _memory_auditor_pass(self) -> int:
        """Chain prior-version reading memories under newer-better ones.

        Steps per pass:
          1. Fetch recent reading-origin memories (FACTUAL/INTERPRETIVE only)
          2. For each, find the nearest sibling by cosine ≥ threshold that
             shares ≥1 graph neighbor
          3. Rank the pair; loser gets a `prior_version_of` edge to winner
          4. Emit a VERSION_CHAIN_<uuid> audit memory

        Returns number of chains created this pass.

        Biomimetic framing: sleep replay + selective revision. The new memory
        is reconsolidated against the old; the old isn't deleted but becomes
        a prior version whose head is superseded. Matches the evolution-of-
        understanding pattern over time.
        """
        import uuid as _uuid
        from datetime import datetime as _dt

        try:
            from ..cognition.embedder import (
                cosine_similarity as _cos,
                embed as _embed,
            )
        except Exception:
            return 0  # embedder unavailable — silent skip

        # Pull recent reading-origin FACTUAL/INTERPRETIVE memories.
        # Reading deposits carry source='book_learner' (newer pipeline) or
        # 'reading_indexer' (legacy). Scan the newer ones as candidates to
        # become chain heads; older ones become chain tails.
        try:
            recent_factual = (
                self.cortex.get_by_type_and_source(
                    MemoryType.FACTUAL,
                    "book_learner",
                    limit=self._AUDITOR_BATCH_WINDOW,
                )
                or []
            )
            recent_interp = (
                self.cortex.get_by_type_and_source(
                    MemoryType.INTERPRETIVE,
                    "book_learner",
                    limit=self._AUDITOR_BATCH_WINDOW,
                )
                or []
            )
        except Exception:
            return 0
        candidates = list(recent_factual) + list(recent_interp)
        if len(candidates) < 2:
            return 0

        # Batch-fetch embeddings; compute any missing.
        cand_ids = [m.id for m in candidates]
        emb_map = self.cortex._get_embeddings_batch(cand_ids)
        vecs: dict = {}
        for m in candidates:
            v = emb_map.get(m.id)
            if v is None:
                try:
                    v = _embed(m.narrative or "")
                except Exception:
                    v = None
            if v is not None:
                vecs[m.id] = v

        # Pairwise scan. To keep cost bounded, only the newest ~50 acts as
        # potential HEADS; each is compared against the older rest as
        # potential tails. This is quadratic in 50×batch but batch≤100 so
        # ~5000 cosine comparisons — fine.
        now = _dt.now()

        def _age(m) -> float:
            try:
                ts = _dt.fromisoformat((m.timestamp or "").replace("Z", "+00:00"))
                return max(0.0, (now - ts.replace(tzinfo=None)).total_seconds())
            except Exception:
                return 1e6

        sorted_cands = sorted(candidates, key=_age)  # newest first
        heads = sorted_cands[:50]
        tails = sorted_cands[50:]

        chains_made = 0
        already_chained: set = set()

        for head in heads:
            if chains_made >= self._AUDITOR_MAX_CHAINS_PER_PASS:
                break
            if head.id in already_chained:
                continue
            if head.id not in vecs:
                continue
            best_match = None
            best_score = self._AUDITOR_COSINE_THRESHOLD
            for tail in tails:
                if tail.id in already_chained:
                    continue
                if tail.id not in vecs:
                    continue
                sim = _cos(vecs[head.id], vecs[tail.id])
                if sim < best_score:
                    continue
                # Shared neighbor check: at least one parent OR child in common
                try:
                    head_parents = {head.parent_id} if head.parent_id else set()
                    tail_parents = {tail.parent_id} if tail.parent_id else set()
                    head_children = set(head.children_ids or [])
                    tail_children = set(tail.children_ids or [])
                    if not (
                        (head_parents & tail_parents) or (head_children & tail_children)
                    ):
                        continue
                except Exception as _sim_e:
                    log.debug(
                        "NE._find_best_tail_match: similarity check failed: %s", _sim_e
                    )
                    continue
                best_score = sim
                best_match = tail

            if best_match is None:
                continue

            # Rank — does head actually win, or does tail deserve to stay head?
            h_meta = head.metadata or {}
            t_meta = best_match.metadata or {}
            h_conf = float(h_meta.get("extraction_confidence", 0.5) or 0.5)
            t_conf = float(t_meta.get("extraction_confidence", 0.5) or 0.5)
            h_rank = self._audit_rank(
                h_conf, self._provenance_completeness(h_meta), _age(head)
            )
            t_rank = self._audit_rank(
                t_conf, self._provenance_completeness(t_meta), _age(best_match)
            )
            if h_rank >= t_rank:
                winner, loser = head, best_match
            else:
                winner, loser = best_match, head

            # Create prior_version_of edge from loser → winner
            try:
                self.cortex.add_interpretive_edge(
                    from_id=loser.id,
                    to_id=winner.id,
                    direction="prior_version_of",
                    weight=float(best_score),
                    layer="version_chain",
                    meaning_payload=(
                        f"Cosine={best_score:.3f}; loser_rank={min(h_rank, t_rank):.3f}; "
                        f"winner_rank={max(h_rank, t_rank):.3f}"
                    )[:400],
                )
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"auditor add_interpretive_edge: {_bare_e}",
                )
                continue

            # Emit audit-trail memory
            try:
                audit_narr = (
                    f"Version chain: {winner.id} (head) ← {loser.id} (prior). "
                    f"cosine={best_score:.3f}"
                )
                audit_mem = Memory(
                    id=f"VERSION_CHAIN_{_uuid.uuid4().hex[:12]}",
                    narrative=audit_narr,
                    memory_type=MemoryType.FACTUAL,
                    parent_id="CP1",  # learning / growth
                    valence=0.1,
                    source="runtime:memory_auditor",
                    metadata={
                        "deposited_by": "runtime:memory_auditor",
                        "winner_id": winner.id,
                        "loser_id": loser.id,
                        "cosine": round(float(best_score), 4),
                        "winner_rank": round(max(h_rank, t_rank), 4),
                        "loser_rank": round(min(h_rank, t_rank), 4),
                    },
                )
                self.cortex.store(audit_mem)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"auditor audit-mem: {_bare_e}",
                )

            already_chained.add(loser.id)
            chains_made += 1

        return chains_made

    # ── #310: Night consolidation (idle deep pass) ────────────────────────────

    def notify_interactive(self) -> None:
        """
        #310: Called by main.py whenever a real interactive turn starts.
        Resets the idle timer used by consolidation eligibility check.
        """
        self._last_interactive_ts: float = time.monotonic()

    def is_consolidation_eligible(self) -> bool:
        """
        #310: Returns True when Igor has been idle long enough to warrant a deep pass.
        Gate: IGOR_CONSOLIDATION_IDLE_MIN (default 20 min).
        False if a deep pass is already running.
        """
        idle_min = float(os.getenv("IGOR_CONSOLIDATION_IDLE_MIN", "20"))
        idle_sec = idle_min * 60.0
        last_ts = getattr(self, "_last_interactive_ts", None)
        if last_ts is None:
            # Never had an interactive turn — not eligible (may be booting)
            return False
        elapsed = time.monotonic() - last_ts
        if elapsed < idle_sec:
            return False
        if getattr(self, "_consolidation_running", False):
            return False
        # Don't re-run until another full idle period after last consolidation
        last_consol = getattr(self, "_last_consolidation_ts", None)
        if last_consol is not None and (time.monotonic() - last_consol) < idle_sec:
            return False
        return True

    def _deep_consolidation_pass(self) -> dict:
        """
        #310: Deep offline consolidation pass. Runs when idle >= IGOR_CONSOLIDATION_IDLE_MIN.

        Steps:
          1. Promote TWM observations at lower threshold (0.5 instead of 0.7)
          2. Episodic cluster merge (lower threshold: IGOR_NE_MERGE_THRESHOLD or 0.80)
          3. Prune weak links (weight < 0.05, last_accessed > 10 days ago)
          4. run_node_adoption() if IGOR_NODE_ADOPTION_ENABLED=true
          5. integrate_reading() for any unembedded reading nodes

        Yields on each step to check _consolidation_interrupted flag.
        Logs results to cognition_metrics.log.
        Returns dict of counts per step.
        """
        import os as _os
        import time as _time

        self._consolidation_running = True
        self._consolidation_interrupted = False
        t0 = _time.perf_counter()
        counts = {
            "promoted": 0,
            "merged": 0,
            "pruned": 0,
            "adopted": 0,
            "reading_integrated": 0,
        }

        try:
            from .forensic_logger import log_anomaly as _la

            _la(kind="CONSOLIDATION_START", detail="idle deep pass beginning")
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"narrative_engine.py:1732: {_exc}")

        # Step 1: TWM promotion at 0.5 threshold
        if not self._consolidation_interrupted:
            try:
                _all_raw = self.cortex.twm_read(limit=200, include_integrated=True)
                raw_obs = self._filter_obs(_all_raw)
                _low_thresh = 0.5
                for cand_obs in raw_obs:
                    if self._consolidation_interrupted:
                        break
                    sal = cand_obs.get("salience", 0.0)
                    if sal < _low_thresh:
                        continue
                    content = cand_obs.get("content_csb", "")
                    if self._is_self_diagnostic(content):
                        continue
                    if any(content.startswith(p) for p in _NE_CONTENT_PREFIXES):
                        continue
                    # Promote as FACTUAL node at lower importance bar
                    mem = Memory(
                        narrative=content[:2000],
                        memory_type=MemoryType.FACTUAL,
                        parent_id="CP3",
                        metadata={
                            "source": "consolidation_pass",
                            "promoted_at": datetime.now().isoformat(),
                            "twm_salience": sal,
                        },
                    )
                    self.cortex.store(mem)
                    counts["promoted"] += 1
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._deep_consolidation_pass step1: {_bare_e}",
                )

        # Step 2: Episodic merge at lower cosine threshold
        if not self._consolidation_interrupted:
            try:
                _orig_threshold = _os.environ.get("IGOR_NE_MERGE_THRESHOLD")
                _os.environ["IGOR_NE_MERGE_THRESHOLD"] = str(
                    min(float(_orig_threshold or "0.85"), 0.80)
                )
                counts["merged"] = self._consolidation_merge_pass()
                if _orig_threshold is not None:
                    _os.environ["IGOR_NE_MERGE_THRESHOLD"] = _orig_threshold
                else:
                    del _os.environ["IGOR_NE_MERGE_THRESHOLD"]
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._deep_consolidation_pass step2: {_bare_e}",
                )

        # Step 3: Weak link pruning (weight < 0.05, last_accessed > 10 days ago)
        if not self._consolidation_interrupted:
            try:
                cutoff = (datetime.now() - timedelta(days=10)).isoformat()
                with self.cortex._conn() as _conn:
                    rows = _conn.execute(
                        "SELECT id, links_weighted FROM memories "
                        "WHERE links_weighted IS NOT NULL AND links_weighted != '{}' "
                        "AND (last_accessed IS NULL OR last_accessed < %s)",
                        (cutoff,),
                    ).fetchall()
                for row in rows:
                    if self._consolidation_interrupted:
                        break
                    try:
                        links = json.loads(row["links_weighted"] or "{}")
                        pruned_links = {k: v for k, v in links.items() if v >= 0.05}
                        if len(pruned_links) < len(links):
                            with self.cortex._conn() as _conn:
                                _conn.execute(
                                    "UPDATE memories SET links_weighted = %s WHERE id = %s",
                                    (json.dumps(pruned_links), row["id"]),
                                )
                            counts["pruned"] += len(links) - len(pruned_links)
                    except Exception as _prune_e:
                        log.debug(
                            "NE._deep_consolidation_pass: link prune failed: %s",
                            _prune_e,
                        )
                        continue
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._deep_consolidation_pass step3: {_bare_e}",
                )

        # Step 4: Node adoption (orphan linking)
        if not self._consolidation_interrupted:
            if _os.getenv("IGOR_NODE_ADOPTION_ENABLED", "false").lower() == "true":
                try:
                    adopted = self.cortex.adopt_orphans(batch_size=100)
                    counts["adopted"] = adopted
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"narrative_engine._deep_consolidation_pass step4: {_bare_e}",
                    )
            else:
                counts["adopted"] = -1  # -1 = gated off

        # Step 5: integrate_reading() for unembedded nodes
        if not self._consolidation_interrupted:
            try:
                from ..tools.reading_integration import integrate_reading as _ir

                result_str = _ir(batch="50")
                # Parse count from result string if possible
                import re as _re

                m = _re.search(r"(\d+)", result_str or "")
                counts["reading_integrated"] = int(m.group(1)) if m else 1
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"narrative_engine._deep_consolidation_pass step5: {_bare_e}",
                )

        elapsed_ms = int((_time.perf_counter() - t0) * 1000)

        # Log to cognition_metrics.log
        try:
            _log_path = (
                __import__("pathlib").Path.home()
                / ".TheIgors"
                / "logs"
                / "cognition_metrics.log"
            )
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            _line = (
                f"{datetime.now().isoformat()} CONSOLIDATION_DONE "
                f"promoted={counts['promoted']} merged={counts['merged']} "
                f"pruned={counts['pruned']} adopted={counts['adopted']} "
                f"reading={counts['reading_integrated']} "
                f"interrupted={self._consolidation_interrupted} "
                f"elapsed_ms={elapsed_ms}\n"
            )
            with open(_log_path, "a") as _f:
                _f.write(_line)
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"narrative_engine.py:1873: {_exc}")

        try:
            from .forensic_logger import log_anomaly as _la

            _la(
                kind="CONSOLIDATION_DONE",
                detail=(
                    f"promoted={counts['promoted']} merged={counts['merged']} "
                    f"pruned={counts['pruned']} adopted={counts['adopted']} "
                    f"elapsed_ms={elapsed_ms}"
                ),
            )
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"narrative_engine.py:1887: {_exc}")

        self._last_consolidation_ts = time.monotonic()  # prevent immediate re-run
        self._consolidation_running = False
        return counts
