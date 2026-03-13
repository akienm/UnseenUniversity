"""
Narrative Engine (NE) — coherence-checker / meaning-maker.

Runs over TWM (Temporal Working Memory) on trigger:
  - 5+ unintegrated observations pending
  - 5 minutes since last run (max cadence)
  - 30 seconds min interval (don't hammer)

Core question asked each run:
  "What is happening? What does this mean? What should I do?"

Produces:
  - summary_csb: compressed narrative fragment (stored to LTM if important enough)
  - connections: links between observations
  - salience_updates: list of {obs_id, new_salience}
  - memory_candidates: list of {content_csb, importance, memory_type}
  - action_impulses: list of {action, urgency, why}
  - internal_state: affect/valence snapshot

memory_candidates with importance > 0.7 are promoted to LTM automatically.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import reasoning_cache
from .forensic_logger import log_ne_run, cts as _cts

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType

# ── Config ─────────────────────────────────────────────────────────────────────
NE_MODEL              = "ollama"        # label only; actual inference via OllamaReasoner in _call_local()
NE_TRIGGER_OBS        = 5              # Run if >= this many unintegrated obs
NE_MIN_INTERVAL_SEC   = 30             # Minimum seconds between NE runs
NE_MAX_INTERVAL_SEC   = 300            # Maximum seconds between NE runs (5 min)
PROMOTE_THRESHOLD     = 0.7            # importance >= this → goes to LTM

# WO7: NE loop prevention — comprehensive guards

# source_filter: sources whose TWM entries NE must never re-process
# (NE's own output chain — re-reading would cause recursive self-detection)
_NE_EXCLUDED_SOURCES = frozenset({
    "narrative_engine",   # direct NE TWM pushes (action impulses, promoted echoes)
    "ne_loop_guard",      # reserved for any future loop-guard writes
})

# content_filter: content prefixes that identify NE's own output echoing back
# through TWM (even if source field was overwritten or re-surfaced by other agents)
_NE_CONTENT_PREFIXES = (
    "ACTION_IMPULSE|",
    "IMPULSE_QUEUED|",
    "IMPULSE_EXECUTED|",
    "NE_DIAG|",
    "[NE#",
    "NE_OBS_CAPPED|",
)

# diagnostic_filter: keywords that mark self-referential/operational noise
# (change.20a.2, expanded in WO7)
_SELF_DIAG_KEYWORDS = (
    "loop", "stall", "recursive", "detecting own", "consolidation",
    "narrative engine", "ne run", "ne_run",
    "action impulse", "action_impulse",
    "self-detect", "self_detect",
)

# ── Prospective prediction ─────────────────────────────────────────────────────

@dataclass
class ProspectivePrediction:
    """Result of a prospective NE pass — prediction made before a turn is processed."""
    predicted_habit_id: Optional[str]   # None = no habit predicted to fire
    confidence: float = 0.0             # 0.0–1.0
    pre_warmed_memory_ids: list = field(default_factory=list)
    # #50: NE as incremental predictive parser — predicted upcoming topics from word graph
    predicted_search_keys: list = field(default_factory=list)  # top co-occurring words


# ── Prompt token cap ───────────────────────────────────────────────────────────
# token_cap 2000 (WO7): cap observation block at 2000 tokens
# Rough estimate: 4 chars per token. Oldest observations are dropped first (FIFO).
NE_MAX_OBS_CHARS = 8000  # 2000 tokens × 4 chars/token


class NarrativeEngine:
    """
    Coherence-checker. Runs in the main loop. Stateless between runs —
    all state lives in TWM (SQLite).
    """

    def __init__(self, cortex: Cortex, instance_id: str = "wild-0001"):
        self.cortex         = cortex
        self.instance_id    = instance_id
        self._last_run:     Optional[datetime] = None
        self._run_count:    int = 0
        self._last_ne_model: str = NE_MODEL  # #84: updated to actual model on each run
        self._last_prediction: Optional[ProspectivePrediction] = None  # #121

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
        window = " ".join(
            o.get("content_csb", "") for o in recent_obs[-5:]
        ).lower()

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
                    w for w, _ in predictions
                    if len(w) > 3 and "__" not in w and w not in _STOP
                ][:3]
            except Exception:
                pass

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
            delta = 0.0   # correct prediction — no surprise
        elif predicted is None:
            delta = 0.4   # didn't predict a habit but one fired
        elif actual_habit_id is None:
            delta = 0.25  # predicted a habit but nothing fired
        else:
            delta = 0.8   # wrong habit predicted

        # G11: get TWM seed IDs for link reinforcement (also used for salience boost below)
        seed_ids: list = []
        recent_obs: list = []
        try:
            recent_obs = self.cortex.twm_read(limit=5, include_integrated=False)
            seed_ids = [obs["id"] for obs in recent_obs if obs.get("id")]
        except Exception:
            pass

        if delta < 0.1:
            # Correct prediction — reinforce links from predicted habit to co-active seeds
            if predicted and seed_ids:
                try:
                    self.cortex.reinforce_links(predicted, seed_ids, +0.05)
                except Exception:
                    pass
            return

        self.cortex.write_ring(
            f"NE_SURPRISE|predicted={predicted}|actual={actual_habit_id}|delta={delta:.2f}",
            category="ne_prediction",
        )

        # G11: weaken links that led to wrong prediction; reinforce links toward actual habit
        if predicted and seed_ids:
            try:
                self.cortex.reinforce_links(predicted, seed_ids, -0.10)
            except Exception:
                pass
        if actual_habit_id and seed_ids:
            try:
                self.cortex.reinforce_links(actual_habit_id, seed_ids, +0.05)
            except Exception:
                pass

        # Boost salience on recent TWM context proportional to surprise magnitude
        if delta >= 0.4:
            try:
                for obs in recent_obs:
                    boosted = min(1.0, obs["salience"] + delta * 0.3)
                    self.cortex.twm_update_salience(obs["id"], boosted)
            except Exception:
                pass  # salience boost is advisory — never raise

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
        if self._last_run is None or (now - self._last_run).total_seconds() >= NE_MAX_INTERVAL_SEC:
            # WO7: use _filter_obs() — excludes NE-originated sources AND content prefixes
            raw = self.cortex.twm_read(limit=50, include_integrated=True)
            obs_list = self._filter_obs(raw)
            has_meaningful = any(
                o["source"] in ("user_input", "discord", "gmail")
                or o["salience"] >= 0.6
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

        # Mark filtered-out unintegrated obs as integrated so they stop counting
        # toward the trigger threshold. They've been seen — just not processable.
        _filtered_ids = [
            o["id"] for o in _all_raw
            if not o.get("integrated") and o not in raw_obs
        ]
        if _filtered_ids:
            self.cortex.twm_mark_integrated(_filtered_ids)

        # Change 4: sort by urgency * salience — urgent + important items processed first
        obs_list = sorted(
            raw_obs,
            key=lambda o: o.get("urgency", 0.2) * o.get("salience", 0.5),
            reverse=True,
        )

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
                print(f"{_cts()}[NE] Dropped {dropped} oldest obs (prompt token cap, kept {len(obs_list)})")

        if verbose:
            print(f"\n[NE] Running (reason={reason}, obs={len(obs_list)})...")

        # Build CSB prompt
        obs_text = self._format_obs_csb(obs_list)
        last_narrative = self._get_last_narrative()

        prompt = self._build_prompt(obs_text, last_narrative)

        # Watermark for cache invalidation — max obs id already in hand
        max_twm_id = max((o["id"] for o in obs_list), default=0)

        # Call LLM: reasoning cache first, then inference gateway (NE purpose).
        # Gateway routes: cloud_mode active → OR; local NE model set → Ollama → OR fallback.
        result = self._call_inference(prompt, max_twm_id)
        if result is None:
            if verbose:
                print("[NE] Cloud NE call failed — skipping this cycle.")
            try:
                from .forensic_logger import log_anomaly as _la
                _la(kind="NE_FAIL", detail="all_local_and_cloud_failed")
            except Exception:
                pass
            self._last_run = datetime.now()
            return None

        # Process NE output
        promoted, impulses = self._apply_output(result, obs_list, verbose=verbose)

        self._last_run = datetime.now()
        self._run_count += 1

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

    def _apply_output(self, result: dict, obs_list: list[dict], verbose: bool = True) -> tuple[int, int]:
        """Apply NE output: update salience, mark integrated, promote to LTM.
        Returns (promoted_count, impulse_count) for forensic logging."""

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
        promoted = 0
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

            if importance >= PROMOTE_THRESHOLD:
                mem_type_str = cand.get("memory_type", "episodic")
                try:
                    mem_type = MemoryType(mem_type_str)
                except ValueError:
                    mem_type = MemoryType.EPISODIC

                # Track source obs IDs for Signal A TTL extension
                source_obs_id = cand.get("source_obs_id")

                # #66: amygdala analog — tag high-importance memories with current milieu
                _milieu = __import__(
                    "igor.cognition.milieu", fromlist=["get"]
                ).get() if True else None
                try:
                    _ms = _milieu.get_state() if _milieu else None
                except Exception:
                    _ms = None
                _arousal = _ms.arousal if _ms else 0.0
                _valence_enc = _ms.valence if _ms else float(cand.get("valence", 0.0))
                _emotionally_charged = importance >= 0.85 and abs(_arousal) > 0.4

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
                }
                if mem_type == MemoryType.PROCEDURAL and "trigger" not in _meta:
                    _STOP = {"that","this","with","have","from","when","igor","will","akien","then"}
                    _tw = [w.lower().strip(".,?!()[]") for w in content.split() if len(w) > 3]
                    _trigger_words = [w for w in _tw if w not in _STOP][:5]
                    if _trigger_words:
                        _meta["trigger"] = " ".join(_trigger_words)

                mem = Memory(
                    narrative=content,
                    memory_type=mem_type,
                    parent_id=_cp_parent,
                    valence=float(cand.get("valence", 0.0)),
                    arousal=_arousal,
                    metadata=_meta,
                )
                self.cortex.store(mem)
                promoted += 1

                # Signal A (Change 3): extend TTL of source TWM obs when importance >= 0.7
                # The observation was confirmed relevant enough to persist in LTM.
                if source_obs_id is not None:
                    self.cortex.twm_extend_ttl(
                        source_obs_id,
                        reason=f"ne_promoted_importance={importance:.2f}"
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
        _narrative_thread_id = max(_thread_counts, key=_thread_counts.get) if _thread_counts else None

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

        if verbose and (promoted > 0 or summary):
            print(f"{_cts()}[NE] promoted={promoted} to LTM | summary: {summary[:80]}...")

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
            action      = impulse.get("action", "")
            why         = impulse.get("why", "")
            if not action:
                continue
            # Dedup check: if >2 significant words from this action already appear
            # in recently-executed impulses, skip — it's already been handled.
            _action_words = [
                w for w in action.lower().split()
                if len(w) > 3 and w not in {"igor", "will", "akien", "that", "this", "with", "from", "have"}
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

        return promoted, impulse_count

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
            print(f"{_cts()}[NE] JSON parse failed — skipping cycle")
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
            o for o in obs_list
            if o.get("source") not in _NE_EXCLUDED_SOURCES
            and not any(o.get("content_csb", "").startswith(p) for p in _NE_CONTENT_PREFIXES)
        ]

    def _is_self_diagnostic(self, text: str) -> bool:
        """Return True if text contains NE operational/self-diagnostic keywords (WO7, change.20a.2)."""
        low = text.lower()
        return any(kw in low for kw in _SELF_DIAG_KEYWORDS)

    def _format_obs_line(self, o: dict) -> str:
        """Format one TWM observation as a CSB line (shared by _format_obs_csb and _cap_observations)."""
        ts   = o["timestamp"][11:16]  # HH:MM only
        src  = o["source"]
        sal  = f"{o['salience']:.2f}"
        intg = "✓" if o["integrated"] else "·"
        csb  = o["content_csb"][:200]
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

    def _get_last_narrative(self) -> str:
        """Fetch the last NE narrative fragment from ring_memory for continuity."""
        entries = self.cortex.read_ring_memory(limit=20, category="narrative")
        if entries:
            return entries[-1]["content"][:300]
        return "(none — first NE run)"

    def _build_prompt(self, obs_text: str, last_narrative: str) -> str:
        return f"""You are the Narrative Engine for Igor, an AI agent. Your job: make sense of what Igor is experiencing.

IDENTITY GUARD: The subject of all observations is IGOR (not "Claude", not "the AI", not "the model").
Igor is the agent. Akien is the human. Always write from Igor's perspective.
"Igor said...", "Igor learned...", "Igor completed..." — never "Claude learned" or "the model did."

SELF-REF GUARD (WO7): Focus ONLY on external events, user interactions, and Igor's goals.
Do NOT generate content describing your own NE process, loops, recursion, or self-observation.
Do NOT produce action_impulses about the NE itself, its loop detection, or its own operation.

LAST NARRATIVE:
{last_narrative}

CURRENT TWM OBSERVATIONS (✓=integrated, ·=new):
{obs_text}

Answer these three questions, then produce structured output:
1. What is Igor experiencing right now?
2. What does this mean for Igor's goals/state?
3. What (if anything) should Igor do?

Reply with ONLY a JSON object — no other text:
{{
  "summary_csb": "<50-100 word dense summary of Igor's current state and what it means>",
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
  "internal_state": {{"valence": <-1.0 to 1.0>, "arousal": <0.0-1.0>, "notes": "<brief>"}}
}}"""

    def _parse_ne_json(self, text: str) -> Optional[dict]:
        """Extract and parse JSON from LLM response. Returns None if unparseable."""
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return None
