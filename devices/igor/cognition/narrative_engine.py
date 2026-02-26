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

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType

# ── Config ─────────────────────────────────────────────────────────────────────
NE_MODEL              = "llama3.2:1b"   # Fast local model for NE
NE_TRIGGER_OBS        = 5              # Run if >= this many unintegrated obs
NE_MIN_INTERVAL_SEC   = 30             # Minimum seconds between NE runs
NE_MAX_INTERVAL_SEC   = 300            # Maximum seconds between NE runs (5 min)
PROMOTE_THRESHOLD     = 0.7            # importance >= this → goes to LTM


class NarrativeEngine:
    """
    Coherence-checker. Runs in the main loop. Stateless between runs —
    all state lives in TWM (SQLite).
    """

    def __init__(self, cortex: Cortex, instance_id: str = "wild-0001"):
        self.cortex      = cortex
        self.instance_id = instance_id
        self._last_run:  Optional[datetime] = None
        self._run_count: int = 0

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
            # Check if observations have meaningful sources (user_input, not just timer/surfacer)
            obs_list = self.cortex.twm_read(limit=50, include_integrated=True)
            has_meaningful = any(
                o["source"] in ("user_input", "discord", "gmail", "narrative_engine")
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
        should, reason = self.should_run()
        if not should:
            return None

        obs_list = self.cortex.twm_read(limit=50, include_integrated=True)
        if not obs_list:
            self._last_run = datetime.now()
            return None

        if verbose:
            print(f"\n[NE] Running (reason={reason}, obs={len(obs_list)})...")

        # Build CSB prompt
        obs_text = self._format_obs_csb(obs_list)
        last_narrative = self._get_last_narrative()

        prompt = self._build_prompt(obs_text, last_narrative)

        # Call LLM (Ollama local first)
        result = self._call_ollama(prompt)
        if result is None:
            result = self._call_claude_fallback(prompt)
        if result is None:
            if verbose:
                print("[NE] Both Ollama and Claude failed — skipping this cycle.")
            self._last_run = datetime.now()
            return None

        # Process NE output
        self._apply_output(result, obs_list, verbose=verbose)

        self._last_run = datetime.now()
        self._run_count += 1

        return result

    # ── Output processing ──────────────────────────────────────────────────────

    def _apply_output(self, result: dict, obs_list: list[dict], verbose: bool = True):
        """Apply NE output: update salience, mark integrated, promote to LTM."""

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
        promoted = 0
        for cand in result.get("memory_candidates", []):
            importance = float(cand.get("importance", 0.0))
            if importance >= PROMOTE_THRESHOLD:
                mem_type_str = cand.get("memory_type", "episodic")
                try:
                    mem_type = MemoryType(mem_type_str)
                except ValueError:
                    mem_type = MemoryType.EPISODIC

                mem = Memory(
                    narrative=cand["content_csb"],
                    memory_type=mem_type,
                    parent_id="CP3",  # "There's always a why" — NE always has a reason
                    valence=float(cand.get("valence", 0.0)),
                    metadata={
                        "source": "narrative_engine",
                        "importance": importance,
                        "ne_run": self._run_count + 1,
                        "promoted_at": datetime.now().isoformat(),
                    }
                )
                self.cortex.store(mem)
                promoted += 1

        # 4. Write narrative fragment to ring_memory for dashboard/context
        summary = result.get("summary_csb", "")
        if summary:
            self.cortex.write_ring(
                f"[NE#{self._run_count + 1}] {summary[:300]}",
                category="narrative"
            )

        if verbose and (promoted > 0 or summary):
            print(f"[NE] promoted={promoted} to LTM | summary: {summary[:80]}...")

        # 5. Push action impulses back into TWM so they can be acted on
        for impulse in result.get("action_impulses", []):
            urgency = float(impulse.get("urgency", 0.3))
            action  = impulse.get("action", "")
            why     = impulse.get("why", "")
            if action:
                self.cortex.twm_push(
                    source="narrative_engine",
                    content_csb=f"ACTION_IMPULSE|urgency={urgency:.2f}|{action}|why:{why}",
                    salience=urgency,
                    metadata={"type": "action_impulse", "action": action, "why": why},
                    ttl_seconds=300,  # impulses expire in 5 min if unacted
                )

    # ── LLM calls ─────────────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str) -> Optional[dict]:
        """Call local Ollama. Returns parsed dict or None."""
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
            return result
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"[NE] Ollama failed ({elapsed:.1f}s): {e}")
            return None

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
                model="claude-haiku-4-5",
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

    def _format_obs_csb(self, obs_list: list[dict]) -> str:
        """Format TWM observations as a compact CSB block for the LLM prompt."""
        lines = []
        for o in obs_list:
            ts   = o["timestamp"][11:16]  # HH:MM only
            src  = o["source"]
            sal  = f"{o['salience']:.2f}"
            intg = "✓" if o["integrated"] else "·"
            csb  = o["content_csb"][:200]
            lines.append(f"{intg} [{ts}] src={src} sal={sal} | {csb}")
        return "\n".join(lines)

    def _get_last_narrative(self) -> str:
        """Fetch the last NE narrative fragment from ring_memory for continuity."""
        entries = self.cortex.read_ring_memory(limit=20, category="narrative")
        if entries:
            return entries[-1]["content"][:300]
        return "(none — first NE run)"

    def _build_prompt(self, obs_text: str, last_narrative: str) -> str:
        return f"""You are Igor's Narrative Engine. Your job: make sense of what's happening.

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
      "content_csb": "<dense CSB text>",
      "importance": <0.0-1.0>,
      "memory_type": "<episodic|semantic|procedural>",
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
