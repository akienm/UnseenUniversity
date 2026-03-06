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
import time
from datetime import datetime, timedelta
from typing import Optional

import ollama as _ollama

from . import reasoning_cache
from .forensic_logger import log_ne_run

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType

# ── Config ─────────────────────────────────────────────────────────────────────
NE_MODEL              = "gemma3:1b"     # Ollama fallback model (legacy; primary is KoboldCpp via _call_local())
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
        raw_obs = self._filter_obs(
            self.cortex.twm_read(limit=50, include_integrated=True)
        )

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
                print(f"[NE] Dropped {dropped} oldest obs (prompt token cap, kept {len(obs_list)})")

        if verbose:
            print(f"\n[NE] Running (reason={reason}, obs={len(obs_list)})...")

        # Build CSB prompt
        obs_text = self._format_obs_csb(obs_list)
        last_narrative = self._get_last_narrative()

        prompt = self._build_prompt(obs_text, last_narrative)

        # Watermark for cache invalidation — max obs id already in hand
        max_twm_id = max((o["id"] for o in obs_list), default=0)

        # Call LLM (KoboldCpp preferred, Ollama fallback, Claude only if service is down)
        result = self._call_local(prompt, max_twm_id)
        if result is None:
            # Only escalate to cloud if KoboldCpp service is actually down (not just slow).
            # A slow response that returns None means parse failure, not service failure —
            # never pay cloud cost just because local 1B produced bad JSON.
            from .reasoners.koboldcpp_reasoner import is_healthy as _kcc_healthy
            import os as _os
            _kcc_host = _os.getenv("KOBOLDCPP_HOST", "http://localhost:5001")
            if not _kcc_healthy(_kcc_host, timeout=3):
                result = self._call_claude_fallback(prompt)
            else:
                if verbose:
                    print("[NE] KoboldCpp is up but result unparseable — skipping cloud fallback.")
        if result is None:
            if verbose:
                print("[NE] Both Ollama and Claude failed — skipping this cycle.")
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

                mem = Memory(
                    narrative=content,
                    memory_type=mem_type,
                    parent_id="CP3",  # "There's always a why" — NE always has a reason
                    valence=float(cand.get("valence", 0.0)),
                    arousal=_arousal,
                    metadata={
                        "source": "narrative_engine",
                        "importance": importance,
                        "ne_run": self._run_count + 1,
                        "promoted_at": datetime.now().isoformat(),
                        **({"emotionally_charged": True} if _emotionally_charged else {}),
                    }
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
        if summary and (promoted > 0 or actions):
            if self._is_self_diagnostic(summary):
                self.cortex.write_ring(
                    f"NE_DIAG|[NE#{self._run_count + 1}] {summary[:300]}",
                    category="ne_diagnostic",
                )
            else:
                self.cortex.write_ring(
                    f"[NE#{self._run_count + 1}] {summary[:300]}",
                    category="narrative"
                )

        if verbose and (promoted > 0 or summary):
            print(f"[NE] promoted={promoted} to LTM | summary: {summary[:80]}...")

        # 5. Push action impulses back into TWM so they can be acted on
        impulse_count = 0
        for impulse in result.get("action_impulses", []):
            imp_urgency = float(impulse.get("urgency", 0.3))
            action      = impulse.get("action", "")
            why         = impulse.get("why", "")
            if action:
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

    def _call_local(self, prompt: str, max_twm_id: int = 0) -> Optional[dict]:
        """
        Call local inference: KoboldCpp preferred, Ollama fallback (Change 1 / D025).
        Checks reasoning cache first.
        """
        # ── Cache check ───────────────────────────────────────────────────────
        cached = reasoning_cache.get(NE_MODEL, prompt, max_twm_id)
        if cached is not None:
            result = self._parse_ne_json(cached)
            if result is not None:
                print(f"[NE] local cache hit (twm_id≤{max_twm_id})")
                return result

        # ── KoboldCpp preferred (Change 1) ────────────────────────────────────
        import os as _os
        kcc_host = _os.getenv("KOBOLDCPP_HOST", "").strip()
        if kcc_host:
            t0 = time.perf_counter()
            try:
                from .reasoners.koboldcpp_reasoner import KoboldCppReasoner
                # NE is background — take as long as needed (120s), never escalate for speed
                kcc = KoboldCppReasoner(host=kcc_host, timeout=120)
                text, _ = kcc.reason(
                    user_input=prompt,
                    relevant_memories=[],
                    core_patterns=[],
                    instance_id=self.instance_id,
                    cortex=self.cortex,
                )
                elapsed = time.perf_counter() - t0
                result = self._parse_ne_json(text)
                if result is not None:
                    print(f"[NE] KoboldCpp ok ({elapsed:.1f}s)")
                    _kcc_model = _os.getenv("KOBOLDCPP_MODEL", "koboldcpp/llama-3.2-1b")
                    reasoning_cache.put(_kcc_model, prompt, text, max_twm_id)
                    self._last_ne_model = _kcc_model  # #84: track actual model used
                    return result
            except Exception as e:
                elapsed = time.perf_counter() - t0
                print(f"[NE] KoboldCpp failed ({elapsed:.1f}s): {e} — falling back to Ollama")

        # ── Ollama fallback ───────────────────────────────────────────────────
        t0 = time.perf_counter()
        try:
            response = _ollama.chat(
                model=NE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3},
            )
            elapsed = time.perf_counter() - t0
            text = response["message"]["content"].strip()
            result = self._parse_ne_json(text)
            if result is not None:
                print(f"[NE] Ollama ok ({elapsed:.1f}s)")
                reasoning_cache.put(NE_MODEL, prompt, text, max_twm_id)
                self._last_ne_model = NE_MODEL  # #84
            return result
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"[NE] Ollama failed ({elapsed:.1f}s): {e}")
            return None

    # Keep _call_ollama as alias for backwards compatibility
    def _call_ollama(self, prompt: str, max_twm_id: int = 0) -> Optional[dict]:
        """Alias for _call_local (KoboldCpp preferred, Ollama fallback)."""
        return self._call_local(prompt, max_twm_id)

    def _call_claude_fallback(self, prompt: str) -> Optional[dict]:
        """Fall back to Claude if Ollama fails and budget allows."""
        try:
            from ..tools.budget import budget_status
            status = budget_status()
            if status["remaining_usd"] < 0.50:
                print("[NE] Claude fallback skipped — budget critical")
                return None
        except Exception:
            pass  # Can't check budget — try anyway

        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip()
            result = self._parse_ne_json(text)
            if result is not None:
                print("[NE] Claude fallback ok")
            return result
        except Exception as e:
            print(f"[NE] Claude fallback failed: {e}")
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
        return f"""You are Igor's Narrative Engine. Your job: make sense of what's happening.

SELF-REF GUARD (WO7): Focus ONLY on external events, user interactions, and Igor's goals.
Do NOT generate content describing your own NE process, loops, recursion, or self-observation.
Do NOT produce action_impulses about the NE itself, its loop detection, or its own operation.

LAST NARRATIVE:
{last_narrative}

CURRENT TWM OBSERVATIONS (✓=integrated, ·=new):
{obs_text}

Answer these three questions, then produce structured output:
1. What is happening right now?
2. What does this mean for Igor's goals/state?
3. What (if anything) should Igor do?

Reply with ONLY a JSON object — no other text:
{{
  "summary_csb": "<50-100 word dense summary of current state and meaning>",
  "connections": ["<observation pattern or link noticed>"],
  "salience_updates": [{{"obs_id": <int>, "new_salience": <0.0-1.0>}}],
  "memory_candidates": [
    {{
      "content_csb": "<key points only — what happened, what it means, enough to find more later; NOT verbatim; max 2 sentences>",
      "importance": <0.0-1.0>,
      "memory_type": "<choose: episodic=one-time event; interpretive=meaning/insight; procedural=recurring pattern or HOW TO do something; factual=stable reference fact>",
      "valence": <-1.0 to 1.0>
    }}
  ],
  "action_impulses": [{{"action": "<what to do>", "urgency": <0.0-1.0>, "why": "<reason>"}}],
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
