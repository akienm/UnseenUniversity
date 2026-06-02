"""
Push Sources — processes that deposit observations into TWM proactively.

Sources:
  HeartbeatSource — Igor's anterior cingulate on a clock (change.31).
                    Replaces TimerSentinel. Checks time, budget, HEARTBEAT
                    procedural memories, and fires proactive Discord alerts.
  MemorySurfacer  — surfaces relevant LTM memories into TWM as background context
  UserInputSource — wraps incoming messages as TWM observations (explicit call)
  MachinesWatcher — watches machines.json for cluster state changes
  InboxWatcher    — watches inbox directory for new files (5s)
  MilieuSource          — pushes ambient emotional state into TWM (60s timer)
  SelfObservationSource — watches Igor's own output for inward watch habit patterns (#243)
  CuriositySource       — fires idle-curiosity impulse when TWM has no active focus (#246)
  ConsolidationReplay   — replays FACT_CLOUD nodes during quiet periods, strengthens co-occurrence edges (D228)
  ThreadCoherenceSource — measures context retention across turns via bg_scoring.top node overlap (T-thread-coherence)
  ProprioceptionSource  — keeps TOOL_REGISTRY_ROOT + facia neighbors warm in TWM (tools are body parts, not lookup table)
  SelfTestSource        — scans blob_index for untested items, runs consolidate_content() every 15min (T-self-test-wire)

All push via cortex.twm_push(). None of them block or crash the main loop.
"""

import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..igor_base import IgorBase
from ..paths import paths
from .forensic_logger import log_anomaly, log_error

# ── T-twm-attentional-gating: conversation mode constants ──────────────────────
# See cortex.twm_push() for the gate implementation. UserInputSource sets
# cortex._conversation_active_ts on each message; twm_push gates background
# sources during active conversation (Baars GWT: workspace is gated).
CONVERSATION_ACTIVE_SEC = 300  # 5 min full gating
CONVERSATION_DECAY_SEC = 600  # 10 min linear opening (5-15 min total)
CONVERSATION_BG_CAP = 0.15  # sub-attentional cap during active conversation
CONVERSATION_ALERT_THRESHOLD = 0.85  # urgency above this always breaks through

MACHINES_JSON = paths().machines_json
INBOX_DIR = paths().inbox

# ── T-procedural-shared-cache: shared PROCEDURAL memory cache (30s TTL) ──────
# HabitCandidateSource, ResourceMonitorSource, SelfObservationSource, and
# CuriositySource all call cortex.get_by_type(PROCEDURAL) independently — 4+
# full table scans per minute. This cache funnels all callers to one scan per
# 30s. Write-through invalidation: call invalidate_procedural_cache() after
# any cortex.store() that adds/modifies a PROCEDURAL memory.
_procedural_cache: list | None = None
_procedural_cache_ts: float = 0.0
_PROCEDURAL_CACHE_TTL: float = 30.0


def get_cached_procedural(cortex) -> list:
    """Return cached PROCEDURAL memories, refreshing if older than 30s."""
    global _procedural_cache, _procedural_cache_ts
    now = time.monotonic()
    if (
        _procedural_cache is None
        or (now - _procedural_cache_ts) >= _PROCEDURAL_CACHE_TTL
    ):
        from ..memory.models import MemoryType as _MT

        _procedural_cache = cortex.get_by_type(_MT.PROCEDURAL)
        _procedural_cache_ts = now
    return _procedural_cache


def invalidate_procedural_cache() -> None:
    """Force next get_cached_procedural() to re-fetch from DB."""
    global _procedural_cache_ts
    _procedural_cache_ts = 0.0


# ── Base ──────────────────────────────────────────────────────────────────────


class BasePushSource(IgorBase):
    name: str = "unnamed_source"
    # T-oscillatory-timing-tiers: biological timing tier for this source.
    # fast (2s)   — interoception, milieu, inbox: near-real-time body/environment sensing
    # medium (30s) — memory surfacing, NE consolidation, resource monitoring
    # slow (300s)  — strategic review, boredom, habit candidate discovery
    # run_background_sources() gates entire tier groups by wall-clock interval.
    TIMING_TIER: str = "medium"

    def push(self, cortex) -> list[int]:
        """
        Run the source. Push observations to TWM if warranted.
        Returns list of new TWM obs IDs (empty if nothing pushed).
        """
        raise NotImplementedError

    def milieu_scale(self, salience: float, urgency: float) -> tuple[float, float]:
        """
        T-milieu-source-aware-salience: Scale salience/urgency by current milieu.

        Under high arousal (stress/overload), low-priority background observations
        are suppressed so the NE's attention narrows to urgent signals.
        Under low arousal (idle/calm), background observations are boosted slightly
        to fill the quiet-period TWM with exploratory content.

        Scaling rule:
          arousal in [-1, 1]; baseline 0.
          high arousal (>0.3)  → suppress: salience × (1 - 0.3 × arousal_excess)
          low arousal  (<-0.2) → boost:    salience × (1 + 0.15 × idle_depth)
          neutral              → no change (scale=1.0)

        Urgency is not scaled — only salience (background relevance) changes.
        Returns (scaled_salience, urgency) clamped to [0.0, 1.0].
        Fail-open: returns unmodified values if milieu is unavailable.
        """
        try:
            from . import milieu as _milieu_mod

            m = _milieu_mod.get()
            if m is None:
                return salience, urgency
            state = m.get_state()
            arousal = state.arousal  # [-1, 1]
            if arousal > 0.3:
                # Suppress background noise under stress
                excess = arousal - 0.3
                scale = max(0.3, 1.0 - 0.3 * excess)
            elif arousal < -0.2:
                # Boost exploratory surfacing during idle
                idle_depth = abs(arousal + 0.2)
                scale = min(1.2, 1.0 + 0.15 * idle_depth)
            else:
                scale = 1.0
            return (
                max(0.0, min(1.0, salience * scale)),
                urgency,  # urgency unchanged — it signals criticality, not relevance
            )
        except Exception:
            return salience, urgency  # fail-open


# ── MemorySurfacer ─────────────────────────────────────────────────────────────


class MemorySurfacer(BasePushSource):
    """
    Surfaces relevant LTM memories into TWM at low salience.

    Reads recent ring entries for keywords, searches LTM, pushes
    matches as background context (salience 0.3-0.6).
    Rate-limited to MIN_INTERVAL_SEC so it doesn't spam.

    change.43: deduplication — tracks recently surfaced memory IDs
    to avoid repetition cycles when ring context is static.
    """

    name = "memory_surfacer"
    MIN_INTERVAL_SEC = 120  # At most every 2 minutes
    SURFACE_WINDOW = 10  # Remember last N surface runs to deduplicate (change.43)

    _STOP = {
        "from",
        "that",
        "with",
        "this",
        "have",
        "been",
        "will",
        "were",
        "they",
        "what",
        "when",
        "where",
        "which",
        "there",
        "their",
        "about",
        "could",
        "would",
        "should",
        "intent",
        "friction",
        "igor",
        "user",
        "akien",
    }

    def __init__(self):
        super().__init__()
        self._last_run: Optional[datetime] = None
        self._last_ring_snapshot: Optional[str] = None  # Detect stale ring (change.43)
        self._recent_surfaced: list[set] = (
            []
        )  # Dedup window: [set(mem_ids), ...] (change.43)
        self._force_push_used: bool = False  # one force_push per NE cycle

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []

        self._last_run = now
        self._force_push_used = False  # reset desperation-pull gate each cycle

        # Pull keywords from recent ring context
        ring = cortex.read_ring_memory(limit=5)
        if not ring:
            return []

        combined = " ".join(e["content"] for e in ring)

        # change.43: detect stale ring — if unchanged since last run, skip push
        # (avoids repetition cycle when human is idle)
        ring_snapshot = combined[:500]
        if ring_snapshot == self._last_ring_snapshot:
            return []  # Ring hasn't changed — no new context to surface
        self._last_ring_snapshot = ring_snapshot

        words = [w.lower() for w in combined.split() if len(w) > 4]
        keywords = [w for w in words if w not in self._STOP]
        if not keywords:
            return []

        top_terms = " ".join(w for w, _ in Counter(keywords).most_common(5))
        candidates = cortex.search(top_terms, limit=5)
        if not candidates:
            return []

        # change.43: dedup — skip memories surfaced in recent window
        recently_surfaced = set()
        for mem_ids_set in self._recent_surfaced:
            recently_surfaced.update(mem_ids_set)

        # Filter out recently surfaced candidates
        new_candidates = [m for m in candidates if m.id not in recently_surfaced]
        if not new_candidates:
            return []  # All candidates were just surfaced — stay quiet

        pushed_ids = set()
        pushed = []
        for mem in new_candidates:
            csb = (
                f"LTM|{mem.memory_type.value}|id={mem.id}|"
                f"inertia={mem.inertia:.2f}|act={mem.activation_count}|"
                f"{mem.narrative[:200]}"
            )
            salience = min(0.6, 0.3 + mem.activation_count * 0.01)
            # T-milieu-source-aware-salience: suppress LTM surfacing under high arousal
            salience, _urg = self.milieu_scale(salience, 0.1)
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=salience,
                metadata={"memory_id": mem.id, "memory_type": mem.memory_type.value},
                ttl_seconds=600,
                urgency=_urg,  # background LTM surfacing — lowest time-sensitivity
            )
            pushed.append(obs_id)
            pushed_ids.add(mem.id)

        # change.43: record this run's surfaced IDs and trim window
        self._recent_surfaced.append(pushed_ids)
        while len(self._recent_surfaced) > self.SURFACE_WINDOW:
            self._recent_surfaced.pop(0)

        # T-cognition-health-metrics: emit infra.metrics data points
        try:
            cortex.record_metric("cognition.ltm_surfaced_count", float(len(pushed)))
        except Exception:
            pass
        try:
            from . import milieu as _m_mod

            _ms = _m_mod.get()
            if _ms is not None:
                _arousal = _ms.get_state().arousal
                if _arousal > 0.3:
                    cortex.record_metric("cognition.arousal_gate_suppression", _arousal)
        except Exception:
            pass

        return pushed

    def force_push(self, cortex, top_n: int = 5) -> list[int]:
        """Desperation pull — surface top-N hot memories bypassing keyword filter.

        Called from coa.py NE-empty branch (T-ne-desperation-pull-ltm).
        Rate-limited to one call per NE cycle via _force_push_used flag;
        flag resets when normal push() runs (MIN_INTERVAL_SEC cadence).
        """
        if self._force_push_used:
            return []
        self._force_push_used = True
        try:
            candidates = cortex.get_hot_nodes(threshold=1, limit=top_n)
            if not candidates:
                return []
            pushed = []
            for mem in candidates:
                csb = (
                    f"LTM_FORCE|{mem.memory_type.value}|id={mem.id}|"
                    f"act={mem.activation_count}|{mem.narrative[:200]}"
                )
                obs_id = cortex.twm_push(
                    source=self.name,
                    content_csb=csb,
                    salience=0.5,
                    metadata={
                        "memory_id": mem.id,
                        "memory_type": mem.memory_type.value,
                        "force_push": True,
                    },
                    ttl_seconds=300,
                    urgency=0.2,
                )
                pushed.append(obs_id)
            return pushed
        except Exception as _e:
            log_error(kind="FORCE_PUSH", detail=str(_e))
            return []


# ── HeartbeatSource ───────────────────────────────────────────────────────────


class HeartbeatSource(BasePushSource):
    """
    Igor's anterior cingulate running on a clock (change.31).
    Replaces TimerSentinel. Every MIN_INTERVAL_SEC seconds:

      1. Pushes time/session tick to TWM at salience 0.4.
      2. Hot-reloads .env if mtime changed (D119 env_sync).
      3. Checks budget — if warn/critical, pushes high-salience alert.
      4. Scans for PROCEDURAL memories with trigger='heartbeat_check'
         and includes their conditions as context.
      5. Sends proactive Discord alert for CRITICAL/EXHAUSTED budget
         (once per level per session to avoid spam).
      6. Arbiter pending items checked via HeartbeatSource._check_arbiter() (change.33).
    """

    name = "heartbeat"
    TIMING_TIER = "slow"
    MIN_INTERVAL_SEC = 300  # 5 minutes

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._session_start: datetime = datetime.now()
        self._discord_alerted: set = set()  # prevent repeat alerts same session
        self._twm_trigger_dispatched: set = set()  # TWM entry IDs already dispatched

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        # G50: decay attractor focus over time — old foci fade every heartbeat
        try:
            cortex.twm_decay_attractor(factor=0.90)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )

        # T-twm-relevance-decay: goal-relevance-weighted TTL shortening every heartbeat
        try:
            cortex.twm_apply_goal_decay()
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py twm_apply_goal_decay: {_bare_e}",
            )

        session_mins = int((now - self._session_start).total_seconds() / 60)
        pushed = []

        # 1. Time/session tick (salience 0.4 — NE should notice, not just log)
        csb = (
            f"HEARTBEAT|{now.strftime('%Y-%m-%dT%H:%M')}|"
            f"day={now.strftime('%A')}|"
            f"session_age={session_mins}min"
        )
        # T-milieu-source-aware-salience: time ticks are low-priority; suppress under stress
        _sal, _urg = self.milieu_scale(0.4, 0.3)
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=_sal,
            metadata={"session_minutes": session_mins},
            ttl_seconds=600,
            urgency=_urg,
        )
        pushed.append(obs_id)

        # 2. .env hot-reload — pick up changes without full restart
        self._check_env_sync(cortex)

        # 3. Budget status check + burn trajectory
        pushed.extend(self._check_budget(cortex))
        pushed.extend(self._check_burn_trajectory(cortex))

        # 4. HEARTBEAT procedural memories (user-defined conditions)
        pushed.extend(self._check_heartbeat_memories(cortex, now))

        # 4b. TWM-trigger habits — fire proactive impulse when TWM contains the key
        pushed.extend(self._check_twm_trigger_habits(cortex))

        # 5. Orphan adoption — link FACTUAL/EPISODIC nodes to nearest CP/ID attractor
        self._run_orphan_adoption(cortex)

        return pushed

    def _check_budget(self, cortex) -> list[int]:
        """Push high-salience budget alert if warn/critical. Fire Discord once per level."""
        try:
            from devices.igor.tools.resource_manager import budget_status

            s = budget_status()
        except Exception:
            return []

        remaining = s["remaining_usd"]
        total = s.get("purchased_usd") or s.get("spending_cap", 0)
        src = s.get("source", "local_tracking")
        if remaining > 10.0 and not s["critical"]:
            return []  # Balance fine — stay quiet (>$10 remaining)

        if remaining <= 0:
            level, salience = "EXHAUSTED", 1.0
            msg = (
                f"Balance EXHAUSTED ({src}): ${remaining:.2f} remaining. "
                f"OpenRouter calls blocked."
            )
        elif s["critical"]:
            level, salience = "CRITICAL", 0.9
            msg = (
                f"Balance CRITICAL ({src}): ${remaining:.2f} remaining of ${total:.2f}."
            )
        else:
            level, salience = "LOW", 0.7
            msg = (
                f"Balance LOW ({src}): ${remaining:.2f} remaining "
                f"({100 - s['pct_used']:.0f}% left)."
            )

        budget_urgency = {"EXHAUSTED": 0.9, "CRITICAL": 0.9, "LOW": 0.5}.get(level, 0.3)
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=f"BUDGET_{level}|{msg}",
            salience=salience,
            metadata={"level": level, "remaining_usd": remaining},
            ttl_seconds=600,
            urgency=budget_urgency,  # Change 4: budget alerts scale with severity
        )

        # Alert once per level per session — Discord (CRITICAL/EXHAUSTED) + cc_alerts (all)
        if level not in self._discord_alerted:
            # #67: cc_alerts.log so CC sees budget warnings at next session start
            try:
                from .forensic_logger import log_anomaly as _la

                _la(kind=f"BUDGET_{level}", detail=msg)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
                )
            if level in ("CRITICAL", "EXHAUSTED"):
                self._alert_discord(f"[Igor heartbeat] {msg}")
            self._discord_alerted.add(level)

        return [obs_id]

    def _check_burn_trajectory(self, cortex) -> list[int]:
        """Push TWM alert if burn rate is high AND days_remaining is low. Once per session."""
        try:
            from devices.igor.tools.resource_manager import get_balance_trajectory

            traj = get_balance_trajectory(window_hours=48.0)
        except Exception:
            return []

        if traj["trend"] == "no_data" or traj["sample_count"] < 2:
            return []

        burn = traj["burn_per_day"]
        dr = traj["days_remaining"]

        # Only alert on burn_fast (>$20/day) OR moderate burn with <3 days remaining
        if burn < 5.0:
            return []
        if dr > 5.0 and burn < 20.0:
            return []  # Moderate burn, plenty of time — stay quiet

        alert_key = f"burn_fast_{int(burn)}"
        if alert_key in self._discord_alerted:
            return []

        dr_str = f"{dr:.1f}d" if dr != float("inf") else "∞"
        msg = (
            f"BURN_TRAJECTORY|${burn:.2f}/day ({traj['trend']}) — "
            f"~{dr_str} remaining at this rate. "
            f"Balance: ${traj['balance_now']:.2f}. "
            f"Window: {traj['oldest_sample_age_h']:.0f}h, {traj['sample_count']} samples."
        )
        urgency = 0.8 if burn > 20 else 0.5
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=msg,
            salience=urgency,
            metadata={"burn_per_day": burn, "days_remaining": dr},
            ttl_seconds=3600,
            urgency=urgency,
        )
        self._discord_alerted.add(alert_key)
        try:
            from .forensic_logger import log_anomaly as _la

            _la(kind="BURN_TRAJECTORY", detail=msg)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )
        return [obs_id]

    def _check_heartbeat_memories(self, cortex, now: datetime) -> list[int]:
        """Push any PROCEDURAL memories with trigger='heartbeat_check' as context."""
        try:
            from ..memory.models import MemoryType

            # T-no-row-scans: SQL filter by trigger metadata
            hb_mems = cortex.get_procedural_by_metadata_key(
                "trigger", value="heartbeat_check"
            )
        except Exception:
            return []

        if not hb_mems:
            return []

        lines = [f"HEARTBEAT_CONDITIONS|{now.strftime('%H:%M')}|count={len(hb_mems)}"]
        for m in hb_mems:
            lines.append(f"  CHECK|{m.id}|{m.narrative[:150]}")
        csb = "\n".join(lines)

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.5,
            metadata={"type": "heartbeat_conditions", "count": len(hb_mems)},
            ttl_seconds=600,
            urgency=0.3,  # Change 4: heartbeat procedural check — not time-critical
        )
        return [obs_id]

    def _check_twm_trigger_habits(self, cortex) -> list[int]:
        """
        D300: Fire proactive impulses for habits that declare `twm_trigger` in metadata.

        For each PROCEDURAL habit with `twm_trigger: "KEY"`, check if TWM has a
        recent non-expired entry with category=key.lower(). If found AND not already
        dispatched for this TWM entry ID, push an ACTION_IMPULSE with source="proactive_habit"
        so _drain_action_impulses() picks it up → BG scores the habit → code_ref fires.

        Deduplication: _twm_trigger_dispatched tracks TWM entry IDs to prevent
        re-firing on the same TWM entry. Eviction from TWM (by TTL expiry)
        clears the entry naturally; dispatched IDs are pruned here to avoid unbounded growth.
        """
        try:
            trigger_habits = cortex.get_procedural_by_metadata_key("twm_trigger")
        except Exception:
            return []

        if not trigger_habits:
            return []

        pushed = []
        for habit in trigger_habits:
            trigger_key = habit.metadata.get("twm_trigger", "")
            if not trigger_key:
                continue
            category = trigger_key.lower()

            # Check TWM for a live entry in this category — query by category directly
            # to avoid ORDER BY id ASC / limit=5 missing newer entries buried under
            # READING_STEW / FACIA entries (D301 fix: category-targeted TWM scan)
            try:
                recent = cortex.twm_read(
                    limit=10, include_integrated=False, category=category
                )
            except Exception:
                continue

            matched = [e for e in recent if trigger_key in e.get("content_csb", "")]
            if not matched:
                continue

            twm_entry = matched[0]
            entry_id = twm_entry["id"]

            # Dedup: don't re-fire for the same TWM entry
            if entry_id in self._twm_trigger_dispatched:
                continue

            self._twm_trigger_dispatched.add(entry_id)
            # Prune dispatched set if it grows large (entries expired long ago)
            if len(self._twm_trigger_dispatched) > 200:
                self._twm_trigger_dispatched.clear()

            # D301 fix: call code_ref directly (like SchedulerSource) instead of
            # routing through ACTION_IMPULSE → TWM → _drain_action_impulses.
            # ACTION_IMPULSE gets buried under READING_STEW (50+ non-integrated entries,
            # oldest-first scan limit=20 never reaches the new high-ID impulse).
            # Trigger condition already confirmed — no BG scoring needed.
            code_ref = habit.metadata.get("code_ref", "")
            if not code_ref:
                continue
            result = self._call_twm_trigger_tool(code_ref, habit_id=habit.id)
            obs_id = cortex.twm_push(
                source="twm_trigger",
                content_csb=f"TWM_TRIGGER_FIRED|{habit.id}|{result[:200]}",
                salience=0.6,
                metadata={
                    "habit_id": habit.id,
                    "twm_trigger": trigger_key,
                    "twm_entry_id": entry_id,
                },
                ttl_seconds=120,
                urgency=0.5,
            )
            pushed.append(obs_id)

        return pushed

    def _call_twm_trigger_tool(self, code_ref: str, habit_id: str = "") -> str:
        """Call a twm_trigger habit's code_ref directly (D301 fix)."""
        try:
            from devices.igor.tools.registry import registry
            from ..tools.engram_log import engram_execution_context

            fn_name = code_ref.split(":")[-1]
            tool = registry.get(fn_name)
            if tool is None:
                return f"[twm_trigger] tool not found: {fn_name}"
            with engram_execution_context(habit_id=habit_id or code_ref):
                return str(tool.fn())
        except Exception as e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"push_sources.HeartbeatSource._call_twm_trigger_tool({code_ref}): {e}",
            )
            return f"[twm_trigger] error calling {code_ref}: {e}"

    def _run_orphan_adoption(self, cortex) -> None:
        """
        T-linking-habit: Link orphaned FACTUAL/EPISODIC nodes to their nearest
        CP/ID attractor via embedding cosine similarity.
        Gate: IGOR_NODE_ADOPTION_ENABLED=true.
        Runs silently — logs to forensic log only if adoptions happen or errors occur.
        """
        import os as _os

        if _os.getenv("IGOR_NODE_ADOPTION_ENABLED", "false").lower() != "true":
            return
        try:
            adopted = cortex.adopt_orphans(batch_size=50)
            if adopted > 0:
                from .forensic_logger import log_anomaly as _la

                _la(
                    kind="ORPHAN_ADOPTED",
                    detail=f"adopted {adopted} nodes into attractor trees",
                )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py HeartbeatSource._run_orphan_adoption: {_bare_e}",
            )

    def _check_env_sync(self, cortex) -> None:
        """Hot-reload .env into os.environ if mtime changed. Non-fatal."""
        try:
            import os
            from ..env_sync import boot_env_sync

            instance_id = os.environ.get("IGOR_INSTANCE_ID", "wild-0001")
            env_path = paths().instance / ".env"
            boot_env_sync(cortex, instance_id, env_path)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )

    def _alert_discord(self, message: str):
        """Best-effort proactive Discord alert. Silently ignores all errors."""
        try:
            import os

            channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
            if not channel_id_str:
                return
            from ..network import discord_bot

            discord_bot.send(int(channel_id_str), message)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )


# ── UserInputSource ───────────────────────────────────────────────────────────


class UserInputSource(BasePushSource):
    """
    Wraps incoming user/network messages as TWM observations.

    Called explicitly via push_message() on each message arrival.
    Higher salience than background sources — user input is relevant now.
    """

    name = "user_input"

    def push(self, cortex) -> list[int]:
        return []  # Not timer-based — use push_message() directly

    def push_message(
        self, cortex, content: str, channel: str = "repl", author: str = "user"
    ) -> int:
        """Push a user/network message into TWM. Returns obs ID.
        G50: sets the message as the current TWM attractor — user input defines current focus.
        T-twm-attentional-gating: user input is highest priority (0.95/0.95).
        Chatbot's primary job IS conversation — this must dominate the workspace.
        """
        # T-twm-attentional-gating: mark conversation active on cortex
        cortex.mark_conversation_active()
        # T-long-horizon-alignment-guard: Akien interaction resets the autonomous-cycle counter
        try:
            from ..tools.alignment_guard import reset_interaction as _ag_reset

            _ag_reset()
        except Exception:
            pass

        csb = f"MSG|ch={channel}|from={author}|{content[:300]}"
        obs_id = cortex.twm_push(
            source=f"{self.name}:{channel}",
            content_csb=csb,
            salience=0.95,
            metadata={"channel": channel, "author": author},
            ttl_seconds=1800,  # messages stay relevant for 30 min
            urgency=0.95,  # T-twm-attentional-gating: conversation is highest priority
        )
        # G50: every user message becomes the primary attractor — it defines current focus
        if obs_id and obs_id > 0:
            try:
                cortex.twm_set_attractor(obs_id, weight=1.0)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
                )
        return obs_id


# ── MachinesWatcher ───────────────────────────────────────────────────────────


class MachinesWatcher(BasePushSource):
    """
    Watches ~/.unseen_university/local/machines.json for changes.

    Pushes a high-salience TWM observation on first run (so Igor always
    knows the current machine inventory) and again whenever the file's
    modification time changes (e.g. a machine comes online/offline).
    """

    name = "machines_watcher"
    CHECK_INTERVAL_SEC = 30  # Check every 30 seconds

    def __init__(self):
        self._last_check: Optional[datetime] = None
        self._last_mtime: Optional[float] = None  # None = not yet read

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        if not MACHINES_JSON.exists():
            return []

        try:
            current_mtime = MACHINES_JSON.stat().st_mtime
        except OSError:
            return []

        first_run = self._last_mtime is None
        changed = first_run or (current_mtime != self._last_mtime)
        self._last_mtime = current_mtime

        if not changed:
            return []

        try:
            raw = MACHINES_JSON.read_text(encoding="utf-8").strip()
        except OSError:
            return []

        reason = "initial_load" if first_run else "file_changed"
        csb = f"MACHINES_JSON|{reason}|{now.strftime('%Y-%m-%dT%H:%M')}|{raw[:600]}"
        # T-twm-boot-singletons-replace-not-append: only one "current machines
        # state" exists — evict prior rows before pushing fresh. Without this,
        # each boot + each file-change event appends at salience=0.8, stacking.
        cortex.twm_evict_source(self.name)
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.8,
            metadata={
                "path": str(MACHINES_JSON),
                "mtime": current_mtime,
                "reason": reason,
            },
            urgency=0.5,
            ttl_seconds=3600,
        )
        return [obs_id]


# ── InboxWatcher ──────────────────────────────────────────────────────────────


class InboxWatcher(BasePushSource):
    """
    Watches the inbox directory for new files every CHECK_INTERVAL_SEC seconds.

    Pushes a high-salience TWM observation (0.9) when new files appear,
    whether dropped via the web UI or copied there by other means.
    """

    name = "inbox_watcher"
    TIMING_TIER = "fast"
    CHECK_INTERVAL_SEC = 5

    def __init__(self):
        self._last_check: Optional[datetime] = None
        # Pre-seed with existing files so restart doesn't re-surface old inbox items
        try:
            self._known_files: set = (
                {p.name for p in INBOX_DIR.iterdir() if p.is_file()}
                if INBOX_DIR.exists()
                else set()
            )
        except OSError:
            self._known_files = set()

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        if not INBOX_DIR.exists():
            return []

        try:
            current = {p.name for p in INBOX_DIR.iterdir() if p.is_file()}
        except OSError:
            return []

        new_files = current - self._known_files
        self._known_files = current

        if not new_files:
            return []

        pushed = []
        for filename in sorted(new_files):
            csb = f"INBOX_FILE|{filename}|{now.strftime('%Y-%m-%dT%H:%M')}"
            urgency, salience = self._urgency_for_file(INBOX_DIR / filename)
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=salience,
                metadata={"filename": filename, "inbox": str(INBOX_DIR)},
                ttl_seconds=3600,
                urgency=urgency,
            )
            pushed.append(obs_id)

        return pushed

    @staticmethod
    def _urgency_for_file(path) -> tuple:
        """
        Peek at the first 512 bytes of an inbox file to determine urgency.
        Returns (urgency, salience).
        - Explicit urgency keywords → (0.8, 0.9)  high
        - Low-priority / background markers → (0.2, 0.4)  background
        - Default → (0.5, 0.6)  medium
        """
        _URGENT_WORDS = {"urgent", "asap", "immediately", "right now", "emergency"}
        _LOW_WORDS = {
            "no rush",
            "background",
            "low priority",
            "when you get a chance",
            "non-urgent",
        }
        try:
            snippet = path.read_text(encoding="utf-8", errors="replace")[:512].lower()
        except OSError:
            return 0.5, 0.6
        if any(w in snippet for w in _URGENT_WORDS):
            return 0.8, 0.9
        if any(w in snippet for w in _LOW_WORDS):
            return 0.2, 0.4
        return 0.5, 0.6


# ── HabitCandidateSource ──────────────────────────────────────────────────────


class HabitCandidateSource(BasePushSource):
    """
    Watches for non-PROC memories with high activation_count (#106/#108).

    Any memory accessed repeatedly is a candidate for habituation.
    When activation_count >= THRESHOLD, surface it to TWM so the NE and
    cloud escalation nudge (#109) can evaluate habit compilation.

    Rate-limited per candidate so the same memory isn't nagged repeatedly.
    """

    name = "habit_candidate"
    TIMING_TIER = "slow"
    MIN_INTERVAL_SEC = 600  # Full pass every 10 minutes
    ACTIVATION_THRESH = 5  # Activations before flagging as candidate
    CANDIDATE_TTL_SEC = 3600  # Don't re-surface a candidate within 1 hour

    # Types ineligible for habituation (structure memories, not behaviour)
    _SKIP_TYPES = {"ROOT", "CORE_PATTERN", "PROCEDURAL"}

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._surfaced_at: dict = {}  # memory_id → datetime last surfaced

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        try:
            candidates = cortex.get_hot_nodes(
                threshold=self.ACTIVATION_THRESH,
                skip_types=self._SKIP_TYPES,
                limit=5,
            )
        except Exception:
            return []

        if not candidates:
            return []

        pushed = []
        for mem in candidates:
            last = self._surfaced_at.get(mem.id)
            if last and (now - last).total_seconds() < self.CANDIDATE_TTL_SEC:
                continue  # surfaced recently — skip

            csb = (
                f"HABIT_CANDIDATE|id={mem.id}|type={mem.memory_type.value}"
                f"|activations={mem.activation_count}|inertia={mem.inertia:.2f}"
                f"|narrative={mem.narrative[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=min(0.7, 0.4 + mem.activation_count * 0.02),
                urgency=0.3,
                ttl_seconds=1800,
                metadata={
                    "memory_id": mem.id,
                    "activation_count": mem.activation_count,
                },
            )
            pushed.append(obs_id)
            self._surfaced_at[mem.id] = now

        return pushed


# ── MilieuSource ──────────────────────────────────────────────────────────────


class MilieuSource(BasePushSource):
    """
    Pushes ambient emotional state into TWM as low-salience background context.
    Runs every 60 seconds. Also applies natural decay each tick so mood drifts
    toward neutral even during idle periods.
    """

    name = "milieu"
    TIMING_TIER = "fast"
    MIN_INTERVAL_SEC = 60

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._prev_snapshot = None  # MilieuState snapshot for delta check

    def push(self, cortex) -> list[int]:
        from . import milieu as milieu_mod
        from . import bliss_integrator

        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        m = milieu_mod.get()
        if m is None:
            return []  # Not yet initialized

        # Natural decay — mood drifts toward neutral without new signals
        m.tick()

        # Wire bliss_integrator: apply pursuit completion effects to milieu
        try:
            bliss = bliss_integrator.get()
            bliss.apply_to_milieu(m)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py MilieuSource bliss_integrator: {_bare_e}",
            )

        state = m.get_state()
        prev = self._prev_snapshot

        # Decide whether to push: significant change OR extreme state
        should_push = (
            prev is None
            or m.delta(prev) > milieu_mod.PUSH_DELTA
            or state.arousal > 0.6
            or state.valence < -0.4
        )

        if not should_push:
            self._prev_snapshot = m.snapshot()
            return []

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=m.state_csb(),
            salience=0.4,
            urgency=0.3,  # Background context; not urgent unless NE decides it is
            ttl_seconds=600,
            metadata={"type": "milieu", "tick": state.tick},
        )
        self._prev_snapshot = m.snapshot()
        result_ids = [obs_id]

        # D101: gradient alert — if arousal has been climbing steadily, the parent steps in
        # Push a high-urgency regulate obs so NE can decay conflicting slots
        try:
            from . import milieu as milieu_mod

            slope = m.gradient("arousal")
            if m.is_arousal_climbing():
                regulate_id = cortex.twm_push(
                    source="milieu_gradient",
                    content_csb=(
                        f"MILIEU_REGULATE|arousal_slope={slope:.3f}"
                        f"|arousal={state.arousal:.2f}|action=regulate"
                    ),
                    salience=0.8,
                    urgency=0.75,
                    ttl_seconds=120,
                    metadata={
                        "type": "milieu_regulate",
                        "arousal_slope": slope,
                        "action_pointer": "regulate,de-escalate",
                    },
                )
                result_ids.append(regulate_id)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )

        return result_ids


# ── ProactiveHabitSource ──────────────────────────────────────────────────────


class ProactiveHabitSource(BasePushSource):
    """
    Fires proactive PROC habits at scheduled intervals (#73/#101).

    PROC memories with metadata habit_type="proactive" may declare a schedule:
      - "session_start"  — fires once per session on first push() call
      - "interval:N"     — fires every N seconds (N is an integer)

    Fires by pushing ACTION_IMPULSE to TWM with source="proactive_habit".
    The main loop's _drain_action_impulses() consumes these and routes them
    through _process() as synthetic impulses, so Igor acts without being asked.

    Example: a "go read confluence" habit with schedule="session_start" will
    trigger Igor to absorb new Confluence content every time he wakes up.
    """

    name = "proactive_habit"
    CHECK_INTERVAL_SEC = 60  # Check schedules every minute

    def __init__(self):
        self._last_check: Optional[datetime] = None
        self._session_fired: set = set()  # habit IDs already fired this session
        self._interval_last: dict = {}  # habit_id → datetime last fired

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        try:
            from ..memory.models import MemoryType

            # T-no-row-scans: SQL filter by habit_type metadata
            proactive = cortex.get_procedural_by_metadata_key(
                "habit_type", value="proactive"
            )
        except Exception:
            return []

        if not proactive:
            return []

        pushed = []
        for habit in proactive:
            action = habit.metadata.get("action", "")
            if not action:
                continue  # nothing to do

            schedule = habit.metadata.get("schedule", "session_start")
            if schedule == "session_start":
                if habit.id in self._session_fired:
                    continue
                should_fire = True
            elif schedule.startswith("interval:"):
                try:
                    interval_sec = int(schedule.split(":", 1)[1])
                except ValueError:
                    continue
                last = self._interval_last.get(habit.id)
                should_fire = (
                    last is None or (now - last).total_seconds() >= interval_sec
                )
            else:
                continue  # unknown schedule type — skip

            if not should_fire:
                continue

            csb = (
                f"ACTION_IMPULSE|PROACTIVE_HABIT|id={habit.id}"
                f"|schedule={schedule}"
                f"|action={action[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.6,
                urgency=0.4,
                ttl_seconds=1800,
                metadata={"habit_id": habit.id, "habit_type": "proactive"},
            )
            pushed.append(obs_id)

            if schedule == "session_start":
                self._session_fired.add(habit.id)
            else:
                self._interval_last[habit.id] = now

        return pushed


# ── SchedulerSource ───────────────────────────────────────────────────────────


class SchedulerSource(BasePushSource):
    """
    T-habit-scheduler: fire tool-based habits (code_ref) on timed intervals.

    Distinct from ProactiveHabitSource (which dispatches via ACTION_IMPULSE).
    SchedulerSource calls the habit's code_ref tool directly and pushes the
    result to TWM at low salience — background infrastructure tick.

    Habits opt-in with metadata key:
      "schedule_interval_sec": <int>   — fire this tool every N seconds

    Example: PROC_WORKER_FOREMAN with schedule_interval_sec=60 means the
    foreman checks the queue every minute without Akien having to ask.

    Result is pushed as SCHEDULER_TICK|<habit_id>|<result[:200]> at salience 0.3.
    TTL=short so stale ticks don't pollute the pipeline.
    """

    name = "scheduler"
    CHECK_INTERVAL_SEC = 30  # Poll schedule every 30s

    def __init__(self):
        self._last_check: Optional[datetime] = None
        self._last_fired: dict = {}  # habit_id → datetime

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        try:
            habits = get_cached_procedural(cortex)
        except Exception:
            return []

        scheduled = [
            h
            for h in habits
            if isinstance(h.metadata.get("schedule_interval_sec"), int)
            and h.metadata.get("code_ref")
        ]
        if not scheduled:
            return []

        pushed = []
        for habit in scheduled:
            interval = habit.metadata["schedule_interval_sec"]
            last = self._last_fired.get(habit.id)
            if last is not None and (now - last).total_seconds() < interval:
                continue  # not yet due

            code_ref = habit.metadata["code_ref"]
            result = self._call_tool(code_ref, habit_id=habit.id)
            self._last_fired[habit.id] = now

            # Push result to TWM at low salience (background tick)
            csb = f"SCHEDULER_TICK|{habit.id}|{result[:200]}"
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.3,
                urgency=0.2,
                ttl_seconds=int(interval * 1.5),  # TTL = 1.5× interval
                metadata={"habit_id": habit.id, "interval_sec": interval},
            )
            pushed.append(obs_id)

        return pushed

    def _call_tool(self, code_ref: str, habit_id: str = "") -> str:
        """Resolve code_ref to a registered tool and call it."""
        try:
            from devices.igor.tools.registry import registry
            from ..tools.engram_log import engram_execution_context

            # code_ref format: "module:fn_name" or just "fn_name"
            fn_name = code_ref.split(":")[-1]
            tool = registry.get(fn_name)
            if tool is None:
                return f"[SCHEDULER] tool not found: {fn_name}"
            with engram_execution_context(habit_id=habit_id or code_ref):
                result = str(tool.fn())
            # Persist error-shaped results so they survive TWM TTL expiry
            _result_lower = result.lower()
            if (
                "no sprint tickets" in _result_lower
                or _result_lower.startswith("error:")
                or _result_lower.startswith("[error]")
            ):
                log_anomaly(
                    kind="SCHEDULER_RESULT",
                    detail=f"{habit_id or code_ref}: {result[:200]}",
                )
            return result
        except Exception as e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"push_sources.SchedulerSource._call_tool({code_ref}): {e}",
            )
            return f"[SCHEDULER] error calling {code_ref}: {e}"


# ── ResourceMonitorSource ─────────────────────────────────────────────────────


class ResourceMonitorSource(BasePushSource):
    """
    Polls machine resource state every CHECK_INTERVAL_SEC seconds.
    Evaluates threshold-type PROC habits (habit_type="threshold") and pushes
    to TWM whenever a habit's condition is met (cpu/ram/swap over threshold).

    TWM TTL is short (habit.metadata["twm_ttl_seconds"], default 120s) so
    stale load readings don't pollute future context after the condition clears.

    Only fires when verdict is warn or critical — stays silent on ok.
    Suppresses repeated pushes for the same habit at the same verdict level
    until that level clears (avoids spamming every 60s).
    """

    name = "resource_monitor"
    CHECK_INTERVAL_SEC = 60

    def __init__(self):
        self._last_check: Optional[datetime] = None
        # habit_id → last verdict level pushed ("warn"/"critical")
        self._last_pushed: dict = {}

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        try:
            habits = get_cached_procedural(cortex)
        except Exception:
            return []

        try:
            from devices.igor.tools.filesystem import (
                evaluate_threshold_habits,
                _resource_load_dict,
            )

            tripped = evaluate_threshold_habits(habits)
        except Exception:
            return []

        if not tripped:
            # Clear suppression for habits that are no longer tripping
            self._last_pushed.clear()
            return []

        pushed = []
        for item in tripped:
            habit = item["habit"]
            current = item["current_value"]
            field = item["field"]
            raw = item["raw"]
            verdict = raw.get("verdict", "warn")
            ttl = int(habit.metadata.get("twm_ttl_seconds", 120))
            surface_tmpl = habit.metadata.get(
                "surface_message",
                f"{field} is at {{current_value}} — check before queuing more work.",
            )
            surface_msg = surface_tmpl.format(current_value=current, field=field, **raw)

            # Suppress if we already pushed this habit at this or higher severity
            prev = self._last_pushed.get(habit.id)
            severity = {"warn": 1, "critical": 2}.get(verdict, 0)
            prev_sev = {"warn": 1, "critical": 2}.get(prev, 0)
            if prev is not None and prev_sev >= severity:
                continue  # already notified, condition hasn't escalated

            urgency = 0.7 if verdict == "critical" else 0.5
            salience = 0.8 if verdict == "critical" else 0.6
            csb = (
                f"THRESHOLD_HABIT|{habit.id}|{verdict.upper()}"
                f"|{field}={current}|{surface_msg}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=salience,
                urgency=urgency,
                ttl_seconds=ttl,
                metadata={
                    "habit_id": habit.id,
                    "field": field,
                    "current_value": current,
                    "verdict": verdict,
                },
            )
            pushed.append(obs_id)
            self._last_pushed[habit.id] = verdict

        # Clear suppression for habits that were not in tripped this cycle
        tripped_ids = {item["habit"].id for item in tripped}
        for hid in list(self._last_pushed):
            if hid not in tripped_ids:
                del self._last_pushed[hid]

        # T-inference-monitor: proactive inference availability check
        pushed.extend(self._check_inference_availability(cortex))

        return pushed

    def _check_inference_availability(self, cortex) -> list[int]:
        """
        T-inference-monitor: Poll cloud + local inference availability.
        When both are down, push high-salience TWM observation so Igor
        knows before the next turn fails. Suppresses repeats until condition clears.

        Cloud = OPENROUTER_API_KEY present AND budget remaining > $0.50.
        Local = Ollama is_healthy() returns True.
        """
        import os as _os

        _KEY = "_inference_unavailable"

        local_ok = False
        try:
            from .inference_gateway import is_local_inference_available

            local_ok = is_local_inference_available()
        except Exception as _e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py is_local_inference_available: {_e}",
            )

        cloud_ok = False
        try:
            if _os.getenv("OPENROUTER_API_KEY", "").strip():
                from devices.igor.tools.resource_manager import budget_status

                cloud_ok = budget_status().get("remaining_usd", 1.0) > 0.50
        except Exception:
            cloud_ok = bool(_os.getenv("OPENROUTER_API_KEY", "").strip())

        if local_ok or cloud_ok:
            # Condition cleared — reset suppression
            self._last_pushed.pop(_KEY, None)
            return []

        # Both down — suppress if already pushed
        if self._last_pushed.get(_KEY) == "critical":
            return []

        try:
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=(
                    "INFERENCE_UNAVAILABLE|CRITICAL"
                    "|cloud_ok=False|local_ok=False"
                    "|Both OpenRouter and local Ollama are unreachable. "
                    "Inference will fail until at least one is restored."
                ),
                salience=0.9,
                urgency=0.9,
                ttl_seconds=180,
                metadata={
                    "kind": "inference_unavailable",
                    "cloud_ok": False,
                    "local_ok": False,
                },
            )
            self._last_pushed[_KEY] = "critical"
            from .forensic_logger import log_anomaly as _la

            _la(
                kind="INFERENCE_UNAVAILABLE",
                detail="cloud_ok=False local_ok=False — ResourceMonitorSource alert pushed",
            )
            return [obs_id]
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py ResourceMonitorSource._check_inference_availability: {_bare_e}",
            )
            return []


# ── SelfObservationSource ─────────────────────────────────────────────────────


class SelfObservationSource(BasePushSource):
    """
    Watches Igor's own output for patterns matching inward watch habits (#243).

    Reads recent IGOR_SAID entries from TWM (source="igor_response") that
    haven't been seen yet, and scores all inward-watch habits
    (habit_type="watch", watch_direction="inward") against each one.

    On match: pushes SELF_OBS_HIT|{habit_id}|{matched_text[:200]} to TWM
    at salience 0.5, urgency 0.3, TTL 300s.

    Deduplication: same (habit_id, content_hash) pair won't fire again
    within DEDUP_TTL_SEC, preventing floods from static response patterns.
    """

    name = "self_observation"
    CHECK_INTERVAL_SEC = 30  # Check every 30 seconds
    DEDUP_TTL_SEC = 300  # Same habit+content won't fire again within 5 min

    def __init__(self):
        self._last_check: Optional[datetime] = None
        self._last_twm_id: int = 0  # cursor — only process newer TWM entries
        # (habit_id, content_hash) → datetime last fired
        self._dedup: dict = {}

    @staticmethod
    def _trigger_matches(trigger: str, text_lower: str) -> bool:
        """Return True if trigger pattern matches text (same logic as basal_ganglia)."""
        trigger_lower = trigger.lower()
        if "|" in trigger_lower:

            def _phrase_ok(phrase: str) -> bool:
                p = phrase.strip()
                return bool(p and re.search(r"\b" + re.escape(p) + r"\b", text_lower))

            return any(_phrase_ok(ph) for ph in trigger_lower.split("|"))
        elif " " in trigger_lower:
            tokens = [t for t in trigger_lower.split() if len(t) >= 5]
            return bool(tokens and any(t in text_lower for t in tokens))
        else:
            return trigger_lower in text_lower

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_check is not None
            and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        # Expire old dedup entries
        expired = [
            k
            for k, t in self._dedup.items()
            if (now - t).total_seconds() >= self.DEDUP_TTL_SEC
        ]
        for k in expired:
            del self._dedup[k]

        # Load inward watch habits
        try:
            habits = get_cached_procedural(cortex)
            inward_habits = [
                h
                for h in habits
                if h.metadata.get("habit_type") == "watch"
                and h.metadata.get("watch_direction") == "inward"
                and h.metadata.get("trigger")
            ]
        except Exception:
            return []

        if not inward_habits:
            return []

        # Read recent TWM entries, filter to IGOR_SAID from igor_response source
        try:
            all_obs = cortex.twm_read(limit=100)
        except Exception:
            return []

        new_obs = [o for o in all_obs if o["id"] > self._last_twm_id]
        if new_obs:
            self._last_twm_id = max(o["id"] for o in new_obs)

        igor_said = [
            o
            for o in new_obs
            if o.get("source") == "igor_response"
            and o.get("content_csb", "").startswith("IGOR_SAID|")
        ]
        if not igor_said:
            return []

        pushed = []
        for obs in igor_said:
            # Extract the response text (after "IGOR_SAID|")
            text = obs["content_csb"][len("IGOR_SAID|") :]
            text_lower = text.lower()

            for habit in inward_habits:
                trigger = habit.metadata.get("trigger", "")
                if not self._trigger_matches(trigger, text_lower):
                    continue

                # Dedup check
                content_hash = hashlib.md5(text[:200].encode()).hexdigest()[:8]
                dedup_key = (habit.id, content_hash)
                if dedup_key in self._dedup:
                    continue
                self._dedup[dedup_key] = now

                label = habit.metadata.get("watch_label", habit.id)
                csb = f"SELF_OBS_HIT|{habit.id}|label={label}|{text[:200]}"
                obs_id = cortex.twm_push(
                    source=self.name,
                    content_csb=csb,
                    salience=0.5,
                    urgency=0.3,
                    ttl_seconds=300,
                    metadata={
                        "habit_id": habit.id,
                        "watch_label": label,
                        "watch_direction": "inward",
                    },
                )
                pushed.append(obs_id)

        return pushed


# ── CuriositySource ───────────────────────────────────────────────────────────


class CuriositySource(BasePushSource):
    """
    Fires curiosity impulses when boredom is present and no goals are active.

    Motivational architecture:
      Boredom pushes (mild negative valence via milieu).
      Goals pull (strong positive — tickets, user requests).
      Curiosity pulls (medium positive) when boredom present + no goals.
      Curiosity's positive valence out-competes boredom's negative — inhibition
      as out-competing, not suppression.

    Topics are intrinsic questions: self-improvement, reading, gap analysis.
    When a curiosity topic absorbs attention, milieu shifts positive.
    """

    name = "curiosity"
    TIMING_TIER = "slow"
    MIN_INTERVAL_SEC = 300  # At most every 5 minutes
    TOPIC_COOLDOWN_SEC = 900  # Same topic won't fire again within 15 min

    # Intrinsic curiosity questions — what Igor wonders about when bored.
    # Round-robin through these. Reading queue items get mixed in dynamically.
    _INTRINSIC_QUESTIONS = [
        "What could I do to work better? Check my recent errors and traces.",
        "What's in my reading queue that I haven't started?",
        "Are there gaps in my knowledge I should fill? Check gap_analysis.",
        "What habits fire most often — are any of them stale or misfiring?",
        "What did I escalate to cloud recently that I could handle locally?",
        "What has Akien been interested in lately that I could learn about?",
    ]

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._topic_cooldowns: dict = {}  # topic → datetime last fired
        self._topic_index: int = 0  # round-robin index

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        # Expire old cooldowns
        expired = [
            t
            for t, ts in self._topic_cooldowns.items()
            if (now - ts).total_seconds() >= self.TOPIC_COOLDOWN_SEC
        ]
        for t in expired:
            del self._topic_cooldowns[t]

        # Gate 1: Is boredom present? Check TWM for BOREDOM_DETECTED.
        boredom_present = False
        try:
            obs_list = cortex.twm_read(limit=20)
            for o in obs_list or []:
                csb = o.get("content_csb", "")
                if "BOREDOM_DETECTED" in csb:
                    boredom_present = True
                    break
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"push_sources.py:1621: {_exc}")

        # Gate 2: No active goals? Goals = TASK_SET entries with urgency >= 0.5
        goals_active = False
        try:
            for o in obs_list or []:
                csb = o.get("content_csb", "")
                if "TASK_SET" in csb and o.get("urgency", 0) >= 0.5:
                    goals_active = True
                    break
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"push_sources.py:1632: {_exc}")

        # Fire only when: bored AND no goals pulling.
        # Also fire (weaker) when simply idle — no boredom required, just nothing happening.
        if goals_active:
            return []  # Goals are pulling — curiosity stays quiet

        if not boredom_present:
            # Not bored, no goals — check if truly idle (low urgency across board)
            max_urg = max((o.get("urgency", 0) for o in (obs_list or [])), default=0)
            if max_urg >= 0.35:
                return []  # Something has urgency — stay quiet

        # Build topic list: intrinsic questions + reading queue items
        topics = list(self._INTRINSIC_QUESTIONS)

        # Mix in reading queue titles if available
        try:
            from ..memory.db_proxy import PGDatabaseProxy

            if isinstance(cortex._db, PGDatabaseProxy):
                with cortex._conn() as conn:
                    rows = conn.execute(
                        "SELECT title FROM reading_list WHERE status != 'completed' "
                        "ORDER BY priority DESC LIMIT 5"
                    ).fetchall()
                for r in rows:
                    topics.append(
                        f"Read: {r['title'] if isinstance(r, dict) else r[0]}"
                    )
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"push_sources.py:1663: {_exc}")

        # Round-robin, skipping topics in cooldown
        topic = None
        for _ in range(len(topics)):
            idx = self._topic_index % len(topics)
            self._topic_index += 1
            candidate = topics[idx]
            if candidate not in self._topic_cooldowns:
                topic = candidate
                break

        if topic is None:
            return []  # All topics in cooldown

        self._topic_cooldowns[topic] = now

        # Nudge milieu positive — curiosity out-competes boredom
        try:
            from . import milieu as milieu_mod

            m = milieu_mod.get()
            if m is not None:
                m.nudge_vad(dv=0.12, da=0.15, dd=0.0)  # positive engagement
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"push_sources.py:1688: {_exc}")

        csb = f"ACTION_IMPULSE|CURIOSITY|topic={topic}" f"|action={topic}"
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.6,
            urgency=0.45,
            ttl_seconds=900,
            metadata={
                "topic": topic,
                "curiosity_source": True,
                "boredom_driven": boredom_present,
            },
        )
        return [obs_id]


class BoredomSource(BasePushSource):
    """
    Fires BOREDOM_DETECTED when milieu arousal has been flat for WINDOW_MINS.

    Calibration (2026-03-23): boredom = SLIGHT negative — mild aversive drift,
    enough to make stillness less comfortable than motion. Too strong = anxiety
    freeze. Too weak = no effect. "Slight is load-bearing."

    Rolling window: tracks last WINDOW_MINS arousal samples (1/min). When the
    mean is below AROUSAL_THRESH the system is in a no-mind attractor. Fire
    BOREDOM_DETECTED → ACTION_IMPULSE → foreman_scan (via PROC_WORKER_FOREMAN
    habit) to check for pending work.

    Also nudges milieu with a slight negative valence so there's mild aversive
    pressure — stillness is slightly less comfortable than doing something.

    COOLDOWN_SEC prevents re-firing within the window duration so we don't
    cascade into anxiety.
    """

    name = "boredom_detector"
    TIMING_TIER = "slow"
    MIN_INTERVAL_SEC = 60  # sample every minute
    WINDOW_MINS = int(os.getenv("IGOR_BOREDOM_WINDOW_MINS", "5"))
    AROUSAL_THRESH = float(os.getenv("IGOR_BOREDOM_AROUSAL_THRESHOLD", "0.08"))
    COOLDOWN_SEC = 1200  # 20-min cooldown after each fire

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._last_fired: Optional[datetime] = None
        self._arousal_window: list[float] = []

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        # Sample current arousal
        try:
            from . import milieu as milieu_mod

            m = milieu_mod.get()
            if m is None:
                return []
            arousal = m.get_state().arousal
        except Exception:
            return []

        # Maintain rolling window (drop samples older than WINDOW_MINS)
        self._arousal_window.append(arousal)
        if len(self._arousal_window) > self.WINDOW_MINS:
            self._arousal_window.pop(0)

        # Need a full window before we judge
        if len(self._arousal_window) < self.WINDOW_MINS:
            return []

        # Cooldown check — don't re-fire within window duration
        if (
            self._last_fired is not None
            and (now - self._last_fired).total_seconds() < self.COOLDOWN_SEC
        ):
            return []

        mean_arousal = sum(self._arousal_window) / len(self._arousal_window)
        if mean_arousal >= self.AROUSAL_THRESH:
            return []  # Still engaged — stay quiet

        # Boredom detected — apply slight aversive nudge
        try:
            from . import milieu as milieu_mod

            m = milieu_mod.get()
            if m is not None:
                m.update(valence=-0.08, friction=0.05)  # slight discomfort of stillness
        except Exception as _e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py BoredomSource milieu nudge: {_e}",
            )

        self._last_fired = now

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=(
                f"BOREDOM_DETECTED|mean_arousal={mean_arousal:.3f}"
                f"|window_mins={self.WINDOW_MINS}"
                f"|action=check_worker_queue"
            ),
            salience=0.6,
            urgency=0.55,
            ttl_seconds=900,
            metadata={
                "type": "boredom_detected",
                "mean_arousal": mean_arousal,
                "action_pointer": "check_worker_queue,foreman_scan",
            },
        )

        # T-boredom-goal-coupling: pull active goal facia as attention
        # attractors. Under CP1 these are SURFACED as candidates, not
        # committed to. Igor's substrate will compete them against the
        # worker queue check and anything else salient. The goal-pull
        # closes the motivational circuit Igor himself diagnosed as
        # missing — "I respond well. I don't yet act spontaneously from
        # internal state."
        goal_obs_ids = self._surface_active_goals(cortex)
        return [obs_id] + goal_obs_ids

    def _surface_active_goals(self, cortex) -> list:
        """Query active goal facia and push the top-1 as a TWM attractor.

        Ranked by cumulative_investment_weight * recency_score. Values
        filter is implicit: only facia with status='active' and
        relationship_type in {goal_aspirational, goal_strategic,
        goal_tactical} are considered — deliberate surfacing, not
        flooding.

        Returns list of obs_ids pushed (empty list on failure — best
        effort, never crashes the boredom detector).
        """
        try:
            from ..tools.goal_graph import _fetch_goal_facia
        except Exception as exc:
            log_error(
                kind="BORED_GOAL_IMPORT_FAIL",
                detail=f"devices/igor/cognition/push_sources.py BoredomSource: {exc}",
            )
            return []

        try:
            goals = _fetch_goal_facia()
        except Exception as exc:
            log_error(
                kind="BORED_GOAL_FETCH_FAIL",
                detail=f"devices/igor/cognition/push_sources.py BoredomSource: {exc}",
            )
            return []

        if not goals:
            return []

        active = [g for g in goals if g["metadata"].get("status") == "active"]
        if not active:
            return []

        # Score each goal: weight * recency_decay
        # recency_decay: 1.0 if touched today, halves every 7 days
        now_iso = datetime.now(timezone.utc).isoformat()
        scored: list[tuple[float, dict]] = []
        for g in active:
            meta = g["metadata"]
            try:
                weight = float(meta.get("cumulative_investment_weight", 0.0))
            except (TypeError, ValueError):
                weight = 0.0

            last_iso = meta.get("last_activity_ts") or ""
            recency = 1.0
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(last_iso)
                    age_days = (
                        datetime.now(timezone.utc) - last_dt
                    ).total_seconds() / 86400.0
                    recency = max(0.1, 0.5 ** (age_days / 7.0))
                except Exception:
                    recency = 0.5

            # Tactical goals have a progress-gap boost: incomplete goals
            # pull harder than near-complete ones (more work remaining
            # → more attention-worthy).
            try:
                progress = float(meta.get("progress", 0.0))
            except (TypeError, ValueError):
                progress = 0.0
            gap = 1.0 - progress  # 1.0 = fresh, 0.0 = done
            score = weight * recency * (0.5 + 0.5 * gap)
            scored.append((score, g))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:1]  # surface ONE — not flooding; substrate can re-query

        pushed: list = []
        for score, g in top:
            meta = g["metadata"]
            display = meta.get("display_name", g["id"])
            desired = meta.get("desired_future_state", "")[:200]
            rtype = meta.get("relationship_type", "goal_unknown")
            try:
                obs = cortex.twm_push(
                    source=self.name,
                    content_csb=(
                        f"ACTIVE_GOAL_SURFACED|facia_id={g['id']}"
                        f"|type={rtype}|name={display}"
                        f"|score={score:.3f}|progress={meta.get('progress', 0.0):.2f}"
                    ),
                    salience=0.65,
                    urgency=0.5,
                    ttl_seconds=1800,
                    metadata={
                        "type": "active_goal_surfaced",
                        "via": "boredom",
                        "facia_id": g["id"],
                        "relationship_type": rtype,
                        "display_name": display,
                        "desired_future_state": desired,
                        "score": score,
                        # CP1: this is a surfaced CANDIDATE, not a commitment.
                        # Substrate will compete it against other attractors.
                        "cp1_provisional": True,
                    },
                )
                pushed.append(obs)
            except Exception as exc:
                log_error(
                    kind="BORED_GOAL_PUSH_FAIL",
                    detail=(
                        f"devices/igor/cognition/push_sources.py "
                        f"BoredomSource goal surface {g['id']}: {exc}"
                    ),
                )
        return pushed


# ── Module singletons + convenience runner ────────────────────────────────────

# ── InteroceptionSource ───────────────────────────────────────────────────────


class InteroceptionSource(BasePushSource):
    """
    T-interoception: continuous VAD gradient from machine resource state.

    ResourceMonitorSource fires discrete threshold alerts (warn/critical).
    InteroceptionSource provides the always-on sub-threshold gradient:
    resource state → milieu nudge (via nudge_vad) → TWM entry.
    Body state becomes affect state — including positive registration for ease.

    Mapping (additive deltas applied via Milieu.nudge_vad):
      cpu  > 85%  → arousal↑↑ + valence↓ + dominance↓   (overload)
      cpu  > 60%  → arousal↑  + dominance↓               (effort/strain)
      cpu  35-60% → mild positive valence                 (capable, responsive)
      cpu  < 35%  → valence↑  + arousal↓                 (ease/calm)
      mem  > 90%  → valence↓↓ + arousal↑                 (high constraint)
      mem  > 70%  → valence↓  + arousal↑                 (pressure)
      disk > 80%  → valence↓ (mild)                       (crowding)
      db_latency_ms > 200  → dominance↓ + arousal↑        (contention)
      infer_latency > 5s   → arousal↑  + valence↓ (mild)  (waiting)
      cluster reachable    → dominance↑ (small)            (agency available)
      cluster unreachable  → dominance↓                    (reduced agency)

    Milieu always nudged when any VAD delta is non-zero (positive included).
    TWM push suppressed when salience < MIN_TWM_SALIENCE (calm is quiet but still felt).

    Temporal accumulation: tracks recent stress samples; sustained high stress
    amplifies arousal proportional to streak length (capped at SUSTAIN_MAX).
    """

    name = "interoception"
    TIMING_TIER = "fast"
    CHECK_INTERVAL_SEC = 30
    MILIEU_PUSH_THRESHOLD = (
        0.02  # Nudge milieu if delta vector norm > this (lower: catches calm+)
    )
    MIN_TWM_SALIENCE = 0.25  # Below this, skip TWM push (calm — stay quiet externally)
    SUSTAIN_WINDOW = 6  # Keep last N stress samples for temporal accumulation
    SUSTAIN_THRESHOLD = 0.35  # Stress level considered "sustained" when > this
    SUSTAIN_MAX = 0.08  # Max arousal boost from sustained load

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._stress_history: list[float] = []  # ring of recent stress values

    def _compute_vad(
        self,
        cpu: float,
        mem: float,
        disk: float,
        db_latency_ms: float,
        infer_latency_s: float,
        cluster_reachable: bool,
    ) -> tuple[float, float, float, float]:
        """
        Map resource readings to (dV, dA, dD, stress) deltas.
        All deltas are small signed floats suitable for nudge_vad().
        """
        d_valence = 0.0
        d_arousal = 0.0
        d_dominance = 0.0

        # CPU — graduated: ease → capable → strain → overload
        if cpu > 85:
            d_arousal += 0.10
            d_valence -= 0.06
            d_dominance -= 0.05
        elif cpu > 60:
            d_arousal += 0.05
            d_dominance -= 0.03
        elif cpu > 35:
            # Capable zone — small positive valence (system is responsive)
            d_valence += 0.02
        else:
            # Idle/easy — ease
            d_valence += 0.04
            d_arousal -= 0.02

        # Memory pressure
        if mem > 90:
            d_valence -= 0.10
            d_arousal += 0.05
        elif mem > 70:
            d_valence -= 0.04
            d_arousal += 0.02

        # Disk crowding (mild)
        if disk > 80:
            d_valence -= 0.02

        # DB latency — contention degrades dominance
        if db_latency_ms > 500:
            d_dominance -= 0.05
            d_arousal += 0.03
        elif db_latency_ms > 200:
            d_dominance -= 0.02
            d_arousal += 0.01

        # Inference latency — waiting erodes ease
        if infer_latency_s > 10:
            d_arousal += 0.04
            d_valence -= 0.03
        elif infer_latency_s > 5:
            d_arousal += 0.02
            d_valence -= 0.01

        # Cluster reachability — agency affects dominance
        if cluster_reachable:
            d_dominance += 0.02
        else:
            d_dominance -= 0.03

        # Compute scalar stress for salience + sustain tracking (cpu-dominant)
        stress = (
            max(0.0, (cpu - 30) / 70) * 0.5
            + max(0.0, (mem - 40) / 60) * 0.30
            + max(0.0, (disk - 60) / 40) * 0.10
            + min(0.05, db_latency_ms / 10000) * 0.10
        )
        return d_valence, d_arousal, d_dominance, stress

    def _sustained_arousal_boost(self, stress: float) -> float:
        """
        Temporal accumulation: if stress has been above SUSTAIN_THRESHOLD for
        multiple consecutive samples, return an extra arousal nudge.
        Proportional to streak length, capped at SUSTAIN_MAX.
        """
        self._stress_history.append(stress)
        if len(self._stress_history) > self.SUSTAIN_WINDOW:
            self._stress_history = self._stress_history[-self.SUSTAIN_WINDOW :]
        # Count trailing high-stress samples (from end)
        streak = 0
        for s in reversed(self._stress_history):
            if s >= self.SUSTAIN_THRESHOLD:
                streak += 1
            else:
                break
        if streak < 2:
            return 0.0
        return min(self.SUSTAIN_MAX, (streak - 1) * 0.015)

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        try:
            from ..tools.system_proxy import system_proxy

            snap = system_proxy.snapshot()
            cpu = snap.cpu_percent
            mem = snap.memory.percent if snap.memory else 0.0
            disk = snap.disk.percent if snap.disk else 0.0
        except Exception:
            return []

        # DB latency — read from cortex db_proxy metrics if available
        db_latency_ms = 0.0
        try:
            if cortex is not None and hasattr(cortex, "_db"):
                metrics = cortex._db.get_metrics()
                db_latency_ms = metrics.get("latency_p50_ms", 0.0) or 0.0
        except Exception as e:
            log_error(
                kind="TOOL_FAIL", detail=f"db metrics fetch failed: {e}"
            )  # non-fatal

        # Inference latency — read from inference_gateway if available
        infer_latency_s = 0.0
        try:
            from .inference_gateway import get_last_latency_s

            infer_latency_s = get_last_latency_s() or 0.0
        except Exception as e:
            log_error(
                kind="TOOL_FAIL", detail=f"inference latency fetch failed: {e}"
            )  # non-fatal

        # Cluster reachability — any machine in machines_json with status ok
        cluster_reachable = False
        try:
            import json as _json

            _mj = MACHINES_JSON
            if _mj and Path(_mj).exists():
                _data = _json.loads(Path(_mj).read_text(encoding="utf-8"))
                _machines = (
                    _data if isinstance(_data, list) else _data.get("machines", [])
                )
                cluster_reachable = any(
                    m.get("status", "") in ("ok", "active", "online")
                    for m in _machines
                    if isinstance(m, dict)
                )
        except Exception as e:
            log_error(
                kind="TOOL_FAIL", detail=f"cluster reachability check failed: {e}"
            )  # non-fatal

        d_valence, d_arousal, d_dominance, stress = self._compute_vad(
            cpu, mem, disk, db_latency_ms, infer_latency_s, cluster_reachable
        )

        # Temporal accumulation: boost arousal when stress is sustained
        d_arousal += self._sustained_arousal_boost(stress)

        # Always nudge milieu when any delta is non-zero (positive states included)
        delta_norm = (d_valence**2 + d_arousal**2 + d_dominance**2) ** 0.5
        if delta_norm >= self.MILIEU_PUSH_THRESHOLD:
            try:
                from . import milieu as milieu_mod

                m = milieu_mod.get()
                if m is not None:
                    m.nudge_vad(d_valence, d_arousal, d_dominance)
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"push_sources.InteroceptionSource milieu nudge: {_bare_e}",
                )

        # TWM push — only when notable (calm is felt via milieu but not surfaced as TWM obs)
        # stress=0 → salience=0 < MIN_TWM_SALIENCE=0.25 → calm stays quiet in TWM
        salience = min(0.85, stress * 0.7)
        if salience < self.MIN_TWM_SALIENCE:
            return []

        csb = (
            f"INTEROCEPTION|cpu={cpu:.0f}%|mem={mem:.0f}%|disk={disk:.0f}%"
            f"|db_p50={db_latency_ms:.0f}ms|infer={infer_latency_s:.1f}s"
            f"|cluster={'ok' if cluster_reachable else 'none'}"
            f"|dV={d_valence:+.2f}|dA={d_arousal:+.2f}|dD={d_dominance:+.2f}"
            f"|stress={stress:.2f}"
        )
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=salience,
            urgency=min(0.7, stress * 0.8),
            ttl_seconds=60,
            metadata={
                "type": "interoception",
                "cpu_pct": cpu,
                "mem_pct": mem,
                "disk_pct": disk,
                "db_latency_ms": db_latency_ms,
                "infer_latency_s": infer_latency_s,
                "cluster_reachable": cluster_reachable,
                "stress": stress,
            },
        )
        return [obs_id]


# ── ThreadCoherenceSource ─────────────────────────────────────────────────────


class ThreadCoherenceSource(BasePushSource):
    """
    T-thread-coherence: measures conversational context retention across turns.

    After each turn, computes a coherence score by comparing the activated
    node sets (bg_scoring.top) between consecutive turns on the same thread.
    Low overlap = thread drift. Pushes THREAD_COHERENCE signal to TWM.

    Substrate: turn_trace.YYYYMMDD.log — reads last 2 entries.
    Node extraction: bg_scoring.top list (habit IDs + WINNOW_* interpretive memories).
    Score: weighted Jaccard — sum(min weights) / sum(max weights) over union.

    TWM signal: THREAD_COHERENCE|score=0.xx|shared=N|prev=M|curr=K|drift=yes/no
      - score >= 0.3  : thread maintained
      - score <  0.15 : drift detected → PROC_THREAD_DRIFT fires

    Rate-limited: checks every CHECK_INTERVAL_SEC; fires once per new turn.
    Cross-thread turns skipped (different thread_id = unrelated context).
    """

    name = "thread_coherence"
    CHECK_INTERVAL_SEC = 30
    DRIFT_THRESHOLD = float(os.getenv("IGOR_THREAD_DRIFT_THRESHOLD", "0.15"))

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._last_turn_id: Optional[str] = None

    def _parse_turn_traces(self, log_path: Path) -> list:
        """Parse turn trace log file. Returns list of trace dicts, oldest first."""
        try:
            text = log_path.read_text(encoding="utf-8")
        except Exception:
            return []
        blocks = [b.strip() for b in text.split("=== END ===") if b.strip()]
        traces = []
        for block in blocks:
            brace = block.find("{")
            if brace == -1:
                continue
            try:
                traces.append(json.loads(block[brace:]))
            except Exception as _exc:
                from .forensic_logger import log_error as _le

                _le(kind="SILENT_EXCEPT", detail=f"push_sources.py:2246: {_exc}")
        return traces

    def _extract_nodes(self, trace: dict) -> dict:
        """Return {node_id: score} from bg_scoring.top."""
        try:
            top = trace.get("bg_scoring", {}).get("top", [])
            return {
                entry["id"]: float(entry["score"])
                for entry in top
                if "id" in entry and "score" in entry
            }
        except Exception:
            return {}

    @staticmethod
    def weighted_jaccard(a: dict, b: dict) -> float:
        """Weighted Jaccard similarity: sum(min) / sum(max) over union of keys."""
        if not a or not b:
            return 0.0
        all_keys = set(a) | set(b)
        numerator = sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in all_keys)
        denominator = sum(max(a.get(k, 0.0), b.get(k, 0.0)) for k in all_keys)
        return numerator / denominator if denominator > 0.0 else 0.0

    def push(self, cortex) -> list:
        now = datetime.now()
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        # Locate today's (or yesterday's) turn trace log
        today = now.strftime("%Y%m%d")
        log_dir = paths().logs
        log_path = log_dir / f"turn_trace.{today}.log"
        if not log_path.exists():
            from datetime import timedelta

            yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
            log_path = log_dir / f"turn_trace.{yesterday}.log"
        if not log_path.exists():
            return []

        traces = self._parse_turn_traces(log_path)
        if len(traces) < 2:
            return []

        curr = traces[-1]
        prev = traces[-2]

        # Skip if already scored this turn
        curr_id = curr.get("turn_id")
        if curr_id and curr_id == self._last_turn_id:
            return []
        self._last_turn_id = curr_id

        # Only compare turns on the same thread (different threads = unrelated context)
        if curr.get("thread_id") != prev.get("thread_id"):
            return []

        curr_nodes = self._extract_nodes(curr)
        prev_nodes = self._extract_nodes(prev)
        if not curr_nodes or not prev_nodes:
            return []

        score = self.weighted_jaccard(prev_nodes, curr_nodes)
        shared = len(set(curr_nodes) & set(prev_nodes))
        is_drift = score < self.DRIFT_THRESHOLD

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=(
                f"THREAD_COHERENCE|score={score:.3f}|shared={shared}"
                f"|prev={len(prev_nodes)}|curr={len(curr_nodes)}"
                f"|drift={'yes' if is_drift else 'no'}"
            ),
            salience=0.55 if is_drift else 0.25,
            urgency=0.45 if is_drift else 0.15,
            ttl_seconds=180 if is_drift else 90,
            metadata={
                "type": "thread_coherence",
                "score": round(score, 4),
                "shared_nodes": shared,
                "curr_turn_id": curr_id,
                "drift": is_drift,
            },
        )
        return [obs_id]


memory_surfacer = MemorySurfacer()
heartbeat_source = HeartbeatSource()


class ProprioceptionSource(BasePushSource):
    """
    T-proprioception-source: keeps tool facia nodes warm in TWM on a slow heartbeat.

    Tools are body parts — they should be continuously present as background
    self-awareness, not retrieved on demand. This is the motor body sense,
    parallel to InteroceptionSource (visceral) and distinct from it.

    Mechanism: finds TOOL_REGISTRY_ROOT in LTM, fetches its graph neighbors
    (INTERP_FACIA_* nodes), pushes each to TWM at low salience (0.35) with
    category=body.motor. Always warm → spreading activation does the rest
    when tool-adjacent topics are live.
    """

    name = "proprioception"
    # Slow heartbeat: tool set rarely changes; no need to refresh every minute.
    # TTL (600s) > interval (300s) keeps nodes warm with a single set in flight.
    CHECK_INTERVAL_SEC = 300
    TOOL_SALIENCE = 0.08  # true background — below arousal suppression gate (0.3)
    TOOL_TTL_SEC = 600

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._last_node_ids: frozenset = frozenset()

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run
            and (now - self._last_run).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        ids = []
        try:
            # Find TOOL_REGISTRY_ROOT by direct ID or search
            root = cortex.get("TOOL_REGISTRY_ROOT")
            root_found = root is not None
            if not root_found:
                results = cortex.search("TOOL_REGISTRY_ROOT tool registry", limit=1)
                root = results[0] if results else None
                root_found = root is not None

            # Fetch facia nodes by ID prefix — direct pattern query, no semantic search.
            # Proprioception is direct knowledge, not retrieval: you know where your arm is.
            facia_nodes = []
            if root_found:
                from ..memory.db_proxy import MEM_COLS

                with cortex._db() as conn:
                    rows = conn.execute(
                        f"SELECT {MEM_COLS} FROM memories WHERE id LIKE %s LIMIT 200",
                        ("INTERP_FACIA_%",),
                    ).fetchall()
                facia_nodes = [cortex._to_memory(r) for r in rows]

            # Skip push when node set is unchanged — nodes still live in TWM within TTL.
            node_ids = frozenset(n.id for n in facia_nodes)
            if root_found and node_ids == self._last_node_ids:
                return []
            self._last_node_ids = node_ids

            # Push root node itself
            if root_found:
                obs_id = cortex.twm_push(
                    source="proprioception",
                    content_csb=f"TOOL_REGISTRY_ROOT|{root.narrative[:120]}",
                    salience=self.TOOL_SALIENCE + 0.02,
                    ttl_seconds=self.TOOL_TTL_SEC,
                    category="body.motor",
                    metadata={"node_id": root.id},
                )
                if obs_id:
                    ids.append(obs_id)

            # Push facia neighbors
            for node in facia_nodes[:30]:
                obs_id = cortex.twm_push(
                    source="proprioception",
                    content_csb=f"FACIA|{node.id}|{node.narrative[:100]}",
                    salience=self.TOOL_SALIENCE,
                    ttl_seconds=self.TOOL_TTL_SEC,
                    category="body.motor",
                    metadata={"node_id": node.id},
                )
                if obs_id:
                    ids.append(obs_id)

            cortex.write_ring(
                f"PROPRIOCEPTION|tools={len(facia_nodes)}|root_found={root_found}",
                category="proprioception",
            )
        except Exception as _e:
            log_error(kind="PROPRIOCEPTION_FAIL", detail=str(_e))

        return ids


class CapabilityAwarenessSource(BasePushSource):
    """
    T-self-capability-awareness (#431): surface Igor's own moves to TWM so
    the reasoning layer reaches for them.

    The four uncertainty strategies (from Akien's personal epistemology —
    anti-PTSD vaccination, CP6 origin) live in Igor's code but not in his
    self-model. This source pushes them as visible moves every ~90s,
    alongside current availability status.

    Strategies:
      1. Ignore (triage) — always runnable (passive)
      2. Devise experiment — runnable iff experiment primitive loads;
         strategy-2 substrate shipped 2026-04-15
      3. Ask + test — ask half via LLM escalation, test half via experiment
         primitive; both now live
      4. Wait for a lever — always runnable (passive via NE/TWM decay)

    Also surfaces recent-capability signals: experiment queue depth, tool
    count. Low salience (0.45) — these are background body-sense markers,
    not urgent pushes. Category self.capabilities for downstream filtering.

    CP grounding:
      CP1 — unavailable strategies say so; no silent assumption
      CP3 — each marker explains what the move IS (why it's a move)
      CP6 — cp1_provisional=True on the marker; the LLM reaching for a
            capability is a candidate, not a commitment
    """

    name = "capability_awareness"
    TIMING_TIER = "slow"
    CHECK_INTERVAL_SEC = 90
    SALIENCE = 0.45
    TTL_SEC = 180

    def __init__(self):
        super().__init__()
        self._last_run: "datetime | None" = None

    def _strategies_snapshot(self) -> list[dict]:
        """Build the four-strategies list with current availability."""
        strategies = [
            {
                "id": "strategy_1_ignore",
                "name": "Ignore / triage below attention",
                "runnable": True,
                "how": "passive — let TWM decay drop it",
            },
            {
                "id": "strategy_4_wait",
                "name": "Wait for a new lever to show itself",
                "runnable": True,
                "how": "passive — NE cycles + TWM decay surface new signal",
            },
        ]
        # Strategy 2: experiment primitive
        try:
            from .experiment_scheduler import ExperimentScheduler  # noqa: F401
            from .experiment import Experiment, ProbeKind  # noqa: F401

            strategies.append(
                {
                    "id": "strategy_2_experiment",
                    "name": "Devise an experiment to resolve uncertainty",
                    "runnable": True,
                    "how": (
                        "construct Experiment(hypothesis, probe); enqueue via "
                        "ExperimentScheduler; tick() runs probe and produces "
                        "Observation; apply_outcome() updates engrams"
                    ),
                }
            )
        except Exception as exc:
            strategies.append(
                {
                    "id": "strategy_2_experiment",
                    "name": "Devise an experiment to resolve uncertainty",
                    "runnable": False,
                    "how": f"experiment primitive not loadable: {exc}",
                }
            )
        # Strategy 3: ask + test
        try:
            from .experiment_scheduler import ExperimentScheduler  # noqa: F401

            strategies.append(
                {
                    "id": "strategy_3_ask_test",
                    "name": "Ask someone, then test their answer",
                    "runnable": True,
                    "how": (
                        "escalate to LLM for the ask half; wrap the returned "
                        "claim as a hypothesis and enqueue an experiment to "
                        "test it before committing"
                    ),
                }
            )
        except Exception:
            strategies.append(
                {
                    "id": "strategy_3_ask_test",
                    "name": "Ask someone, then test their answer",
                    "runnable": False,
                    "how": "test half requires experiment primitive",
                }
            )
        return strategies

    def _experiment_queue_depth(self, cortex) -> dict[str, int]:
        """Return current {status: count} for the experiment queue. Empty
        dict if the table doesn't exist yet (pre-migration) or on error."""
        try:
            with cortex._db() as conn:
                conn.execute(
                    "SELECT status, COUNT(*) FROM experiment_queue GROUP BY status",
                    (),
                )
                rows = conn.fetchall() or []
                return {row[0]: int(row[1]) for row in rows}
        except Exception:
            return {}

    def _tool_count(self) -> int:
        try:
            from devices.igor.tools.registry import registry

            return len(registry._tools)
        except Exception:
            return 0

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (
            self._last_run
            and (now - self._last_run).total_seconds() < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        try:
            strategies = self._strategies_snapshot()
            queue = self._experiment_queue_depth(cortex)
            tool_count = self._tool_count()

            runnable_strategies = [s["id"] for s in strategies if s["runnable"]]
            content = (
                f"SELF_CAPABILITIES strategies={len(runnable_strategies)}/4 "
                f"runnable={runnable_strategies} "
                f"tools={tool_count} "
                f"experiment_queue={queue}"
            )

            obs_id = cortex.twm_push(
                source="capability_awareness",
                content_csb=content,
                salience=self.SALIENCE,
                ttl_seconds=self.TTL_SEC,
                category="self.capabilities",
                metadata={
                    "type": "self_capabilities",
                    "strategies": strategies,
                    "tool_count": tool_count,
                    "experiment_queue": queue,
                    "cp1_provisional": True,
                },
            )
            return [obs_id] if obs_id else []
        except Exception as exc:
            log_error(kind="CAPABILITY_AWARENESS_FAIL", detail=str(exc))
            return []


class SelfTestSource(BasePushSource):
    """
    T-self-test-wire: background daemon that scans blob_index.json for ingested
    items not yet tested, runs consolidate_content() on one per cycle, then pushes
    a LEARNING_GAP TWM observation if miss_rate > 0.5. Fires every 15 minutes.
    """

    name = "self_test"
    TIMING_TIER = "slow"
    _INTERVAL = 900  # 15 minutes

    def __init__(self):
        super().__init__()
        self._last_run: float = 0.0
        self._blob_index_path: "Path | None" = None

    def push(self, cortex) -> list:
        now = time.monotonic()
        if now - self._last_run < self._INTERVAL:
            return []
        self._last_run = now

        try:
            from ..paths import paths as _paths

            self._blob_index_path = _paths().instance / "blob_index.json"
            if not self._blob_index_path.exists():
                return []

            index = {}
            try:
                index = __import__("json").loads(self._blob_index_path.read_text())
            except Exception:
                return []

            # Find first untested content_id
            untested = [
                cid
                for cid, meta in index.items()
                if isinstance(meta, dict) and meta.get("status") == "ingested"
            ]
            if not untested:
                return []

            content_id = untested[0]
            from .self_test import consolidate_content

            results = consolidate_content(content_id)

            ids: list = []
            if not results:
                return ids

            # Aggregate miss rate across chapters
            total_q = sum(r.get("questions", 0) for r in results)
            total_m = sum(r.get("misses", 0) for r in results)
            miss_rate = total_m / total_q if total_q > 0 else 0.0

            if miss_rate > 0.5:
                obs_id = cortex.twm_push(
                    source=self.name,
                    content_csb=(
                        f"LEARNING_GAP|content_id={content_id}"
                        f"|miss_rate={miss_rate:.0%}|questions={total_q}"
                    ),
                    salience=0.65,
                    category="self_test",
                    ttl_seconds=1800,
                )
                if obs_id:
                    ids.append(obs_id)
                cortex.write_ring(
                    f"SELF_TEST|content={content_id}|miss={miss_rate:.0%}|label=gap",
                    category="learning",
                )
            else:
                cortex.write_ring(
                    f"SELF_TEST|content={content_id}|miss={miss_rate:.0%}|label=ok",
                    category="learning",
                )
            return ids

        except Exception as _e:
            log_error(kind="SELF_TEST_FAIL", detail=str(_e))
            return []


class StaleChatLogBackfiller(BasePushSource):
    """
    Keep today's CC chat mirror fresh under ~/.unseen_university/logs/CC.0/.

    Runs cc_log_stop_hook.py every 5min — scans all project dirs so ADC
    sessions are included. Historical files are not rebuilt here; that's
    a /day-close concern.
    """

    name = "stale_chat_log_backfiller"
    TIMING_TIER = "slow"
    REFRESH_INTERVAL_SEC = 300  # T-cc-mirror-5min-today

    def __init__(self):
        self._last_run: Optional[datetime] = None

    def push(self, cortex) -> list[int]:
        now = datetime.now(timezone.utc)
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.REFRESH_INTERVAL_SEC
        ):
            return []
        self._last_run = now

        ids: list[int] = []
        try:
            import subprocess

            # Use cc_log_stop_hook.py — scans all project dirs (not just TheIgors),
            # so ADC-project CC sessions are included. export_chat.py only scanned
            # the TheIgors project and smashed the log when ADC became primary.
            result = subprocess.run(
                [
                    "python3",
                    str(
                        paths().source_root
                        / "lab"
                        / "claudecode"
                        / "cc_log_stop_hook.py"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(Path.home() / "dev/src/UnseenUniversity"),
            )

            if result.returncode == 0:
                obs_id = cortex.twm_push(
                    source=self.name,
                    content_csb=(
                        f"CHAT_LOG_EXPORT|status=success|timestamp={now.isoformat()}"
                    ),
                    salience=0.3,
                    category="maintenance",
                    ttl_seconds=3600,
                )
                if obs_id:
                    ids.append(obs_id)
                cortex.write_ring(
                    f"CHAT_LOG_EXPORT|status=success|ts={now.strftime('%Y-%m-%dT%H:%M')}",
                    category="maintenance",
                )
            else:
                log_error(
                    kind="CHAT_LOG_EXPORT_FAIL",
                    detail=f"cc_log_stop_hook.py failed: {result.stderr[:200]}",
                )

        except Exception as _e:
            log_error(kind="CHAT_LOG_BACKFILLER_FAIL", detail=str(_e))

        return ids


proprioception_source = ProprioceptionSource()
capability_awareness_source = CapabilityAwarenessSource()
self_test_source = SelfTestSource()
stale_chat_log_backfiller = StaleChatLogBackfiller()
user_input_source = UserInputSource()
machines_watcher = MachinesWatcher()
inbox_watcher = InboxWatcher()
milieu_source = MilieuSource()
habit_candidate_source = HabitCandidateSource()
proactive_habit_source = ProactiveHabitSource()
resource_monitor = ResourceMonitorSource()
self_observation_source = SelfObservationSource()
curiosity_source = CuriositySource()
boredom_source = BoredomSource()
interoception_source = InteroceptionSource()
scheduler_source = SchedulerSource()
thread_coherence_source = ThreadCoherenceSource()
consolidation_replay = None  # Lazy loaded to avoid circular import
sleep_consolidation = None  # T-sleep-consolidation: lazy loaded
pr_consolidation_source = None  # T-pr-consolidation-sleep-wiring: lazy loaded
intent_decay_source = None  # T-watchlist-intent-decay: lazy loaded
relationship_drift_source = None  # T-watchlist-relationship-drift: lazy loaded
sleep_clock_source = None  # T-sleep-triggered-by-clock: lazy loaded
state_coherence_source = None  # T-watchlist-internal-state-coherence: lazy loaded
approach_frame_audit_source = None  # T-igor-self-audit-approach-frame: lazy loaded
web_server_watchdog = None  # lazy loaded

# ── T-oscillatory-timing-tiers: hierarchical dispatch ─────────────────────────
# Mirrors biological theta/beta/gamma cortex-BG loops.
# fast (2s)   — interoception, milieu, inbox: continuous body/environment sensing
# medium (30s) — memory surfacing, resource monitoring, NE consolidation
# slow (300s)  — strategic review, boredom, habit candidate discovery
#
# Each tier is gated by wall-clock elapsed time so infrequent main-loop calls
# (e.g. during heavy inference) don't accumulate stale-call debt.

_TIER_INTERVALS: dict[str, float] = {"fast": 2.0, "medium": 30.0, "slow": 300.0}
_tier_last_ts: dict[str, float] = {"fast": 0.0, "medium": 0.0, "slow": 0.0}


def run_background_sources(cortex) -> int:
    """
    Run timer-based sources grouped by timing tier (T-oscillatory-timing-tiers).
    Call once per main loop iteration.

    Tiers dispatched only when their wall-clock interval has elapsed:
      fast   (2s)   — interoception, milieu, inbox
      medium (30s)  — memory surfacing, resource monitoring, scheduling
      slow   (300s) — heartbeat, boredom, habit candidates, self-test

    Returns total count of observations pushed this call.
    Exceptions per-source are swallowed — a broken source must not crash the loop.
    """
    import time as _time

    global consolidation_replay, sleep_consolidation, pr_consolidation_source
    global intent_decay_source, relationship_drift_source, sleep_clock_source
    global state_coherence_source, approach_frame_audit_source, web_server_watchdog
    if consolidation_replay is None:
        from .replay import ConsolidationReplay

        consolidation_replay = ConsolidationReplay()
    if sleep_consolidation is None:
        from .sleep_consolidation import SleepConsolidation

        sleep_consolidation = SleepConsolidation()
    if pr_consolidation_source is None:
        from .pr_consolidation_source import PRConsolidationSource

        pr_consolidation_source = PRConsolidationSource()
    if intent_decay_source is None:
        from .intent_decay_source import IntentDecaySource

        intent_decay_source = IntentDecaySource()
    if relationship_drift_source is None:
        from .relationship_drift_source import RelationshipDriftSource

        relationship_drift_source = RelationshipDriftSource()
    if sleep_clock_source is None:
        from .sleep_clock import SleepClockSource

        sleep_clock_source = SleepClockSource()
    if state_coherence_source is None:
        from .state_coherence_check import StateCoherenceSource

        state_coherence_source = StateCoherenceSource()
    if approach_frame_audit_source is None:
        from .approach_frame_audit import ApproachFrameAuditSource

        approach_frame_audit_source = ApproachFrameAuditSource()
    if web_server_watchdog is None:
        from .web_server_watchdog import WebServerWatchdog

        web_server_watchdog = WebServerWatchdog()

    now_ts = _time.monotonic()
    # Determine which tiers are due this call
    due_tiers: set[str] = set()
    for tier, interval in _TIER_INTERVALS.items():
        if now_ts - _tier_last_ts[tier] >= interval:
            due_tiers.add(tier)
            _tier_last_ts[tier] = now_ts

    if not due_tiers:
        return 0

    pushed = 0
    for src in (
        heartbeat_source,
        memory_surfacer,
        machines_watcher,
        inbox_watcher,
        milieu_source,
        habit_candidate_source,
        proactive_habit_source,
        resource_monitor,
        self_observation_source,
        curiosity_source,
        boredom_source,
        interoception_source,
        proprioception_source,
        capability_awareness_source,
        self_test_source,
        stale_chat_log_backfiller,
        scheduler_source,
        thread_coherence_source,
        consolidation_replay,
        sleep_consolidation,
        pr_consolidation_source,
        intent_decay_source,
        relationship_drift_source,
        sleep_clock_source,
        state_coherence_source,
        approach_frame_audit_source,
        web_server_watchdog,
    ):
        tier = getattr(src, "TIMING_TIER", "medium")
        if tier not in due_tiers:
            continue
        try:
            ids = src.push(cortex)
            pushed += len(ids)
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/push_sources.py: {_bare_e}",
            )
    return pushed
