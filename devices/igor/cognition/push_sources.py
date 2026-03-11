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
  MilieuSource    — pushes ambient emotional state into TWM (60s timer)

All push via cortex.twm_push(). None of them block or crash the main loop.
"""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

MACHINES_JSON = Path.home() / ".TheIgors" / "local" / "machines.json"
INBOX_DIR    = Path.home() / ".TheIgors" / "igor_wild_0001" / "inbox"


# ── Base ──────────────────────────────────────────────────────────────────────

class BasePushSource:
    name: str = "unnamed_source"

    def push(self, cortex) -> list[int]:
        """
        Run the source. Push observations to TWM if warranted.
        Returns list of new TWM obs IDs (empty if nothing pushed).
        """
        raise NotImplementedError


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
    SURFACE_WINDOW = 10     # Remember last N surface runs to deduplicate (change.43)

    _STOP = {
        "from", "that", "with", "this", "have", "been", "will", "were",
        "they", "what", "when", "where", "which", "there", "their",
        "about", "could", "would", "should", "intent", "friction",
        "igor", "user", "akien",
    }

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._last_ring_snapshot: Optional[str] = None  # Detect stale ring (change.43)
        self._recent_surfaced: list[set] = []  # Dedup window: [set(mem_ids), ...] (change.43)

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_run is not None
                and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC):
            return []

        self._last_run = now

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
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=salience,
                metadata={"memory_id": mem.id, "memory_type": mem.memory_type.value},
                ttl_seconds=600,
                urgency=0.1,  # Change 4: background LTM surfacing — lowest time-sensitivity
            )
            pushed.append(obs_id)
            pushed_ids.add(mem.id)

        # change.43: record this run's surfaced IDs and trim window
        self._recent_surfaced.append(pushed_ids)
        while len(self._recent_surfaced) > self.SURFACE_WINDOW:
            self._recent_surfaced.pop(0)

        return pushed


# ── HeartbeatSource ───────────────────────────────────────────────────────────

class HeartbeatSource(BasePushSource):
    """
    Igor's anterior cingulate running on a clock (change.31).
    Replaces TimerSentinel. Every MIN_INTERVAL_SEC seconds:

      1. Pushes time/session tick to TWM at salience 0.4.
      2. Checks budget — if warn/critical, pushes high-salience alert.
      3. Scans for PROCEDURAL memories with trigger='heartbeat_check'
         and includes their conditions as context.
      4. Sends proactive Discord alert for CRITICAL/EXHAUSTED budget
         (once per level per session to avoid spam).
      5. Arbiter pending items checked via HeartbeatSource._check_arbiter() (change.33).
    """
    name = "heartbeat"
    MIN_INTERVAL_SEC = 300  # 5 minutes

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._session_start: datetime = datetime.now()
        self._discord_alerted: set = set()  # prevent repeat alerts same session

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_run is not None
                and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC):
            return []
        self._last_run = now

        # G50: decay attractor focus over time — old foci fade every heartbeat
        try:
            cortex.twm_decay_attractor(factor=0.90)
        except Exception:
            pass

        session_mins = int((now - self._session_start).total_seconds() / 60)
        pushed = []

        # 1. Time/session tick (salience 0.4 — NE should notice, not just log)
        csb = (
            f"HEARTBEAT|{now.strftime('%Y-%m-%dT%H:%M')}|"
            f"day={now.strftime('%A')}|"
            f"session_age={session_mins}min"
        )
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.4,
            metadata={"session_minutes": session_mins},
            ttl_seconds=600,
            urgency=0.3,  # Change 4: HeartbeatSource — scheduled, not time-critical
        )
        pushed.append(obs_id)

        # 2. Budget status check
        pushed.extend(self._check_budget(cortex))

        # 3. HEARTBEAT procedural memories (user-defined conditions)
        pushed.extend(self._check_heartbeat_memories(cortex, now))

        return pushed

    def _check_budget(self, cortex) -> list[int]:
        """Push high-salience budget alert if warn/critical. Fire Discord once per level."""
        try:
            from ..tools.budget import budget_status
            s = budget_status()
        except Exception:
            return []

        remaining = s["remaining_usd"]
        total     = s.get("purchased_usd") or s.get("spending_cap", 0)
        src       = s.get("source", "local_tracking")
        if remaining > total * 0.20 and not s["critical"]:
            return []  # Balance fine — stay quiet

        if remaining <= 0:
            level, salience = "EXHAUSTED", 1.0
            msg = (f"Balance EXHAUSTED ({src}): ${remaining:.2f} remaining. "
                   f"OpenRouter calls blocked.")
        elif s["critical"]:
            level, salience = "CRITICAL", 0.9
            msg = (f"Balance CRITICAL ({src}): ${remaining:.2f} remaining of ${total:.2f}.")
        else:
            level, salience = "LOW", 0.7
            msg = (f"Balance LOW ({src}): ${remaining:.2f} remaining "
                   f"({100 - s['pct_used']:.0f}% left).")

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
            except Exception:
                pass
            if level in ("CRITICAL", "EXHAUSTED"):
                self._alert_discord(f"[Igor heartbeat] {msg}")
            self._discord_alerted.add(level)

        return [obs_id]

    def _check_heartbeat_memories(self, cortex, now: datetime) -> list[int]:
        """Push any PROCEDURAL memories with trigger='heartbeat_check' as context."""
        try:
            from ..memory.models import MemoryType
            mems = cortex.get_by_type(MemoryType.PROCEDURAL)
            hb_mems = [m for m in mems if m.metadata.get("trigger") == "heartbeat_check"]
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

    def _alert_discord(self, message: str):
        """Best-effort proactive Discord alert. Silently ignores all errors."""
        try:
            import os
            channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
            if not channel_id_str:
                return
            from ..network import discord_bot
            discord_bot.send(int(channel_id_str), message)
        except Exception:
            pass


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

    def push_message(self, cortex, content: str,
                     channel: str = "repl", author: str = "user") -> int:
        """Push a user/network message into TWM. Returns obs ID.
        G50: sets the message as the current TWM attractor — user input defines current focus.
        """
        csb = f"MSG|ch={channel}|from={author}|{content[:300]}"
        obs_id = cortex.twm_push(
            source=f"{self.name}:{channel}",
            content_csb=csb,
            salience=0.7,
            metadata={"channel": channel, "author": author},
            ttl_seconds=1800,  # messages stay relevant for 30 min
            urgency=0.7,  # Change 4: user input is time-sensitive
        )
        # G50: every user message becomes the primary attractor — it defines current focus
        if obs_id and obs_id > 0:
            try:
                cortex.twm_set_attractor(obs_id, weight=1.0)
            except Exception:
                pass
        return obs_id


# ── MachinesWatcher ───────────────────────────────────────────────────────────

class MachinesWatcher(BasePushSource):
    """
    Watches ~/.TheIgors/local/machines.json for changes.

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
        if (self._last_check is not None
                and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC):
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
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.8,
            metadata={"path": str(MACHINES_JSON), "mtime": current_mtime, "reason": reason},
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
    CHECK_INTERVAL_SEC = 5

    def __init__(self):
        self._last_check: Optional[datetime] = None
        self._known_files: set = set()

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_check is not None
                and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC):
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
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=0.9,
                metadata={"filename": filename, "inbox": str(INBOX_DIR)},
                ttl_seconds=3600,
                urgency=0.8,  # Change 4: new inbox file — Igor should act on this soon
            )
            pushed.append(obs_id)

        return pushed


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
    MIN_INTERVAL_SEC  = 600   # Full pass every 10 minutes
    ACTIVATION_THRESH = 5     # Activations before flagging as candidate
    CANDIDATE_TTL_SEC = 3600  # Don't re-surface a candidate within 1 hour

    # Types ineligible for habituation (structure memories, not behaviour)
    _SKIP_TYPES = {"ROOT", "CORE_PATTERN", "PROCEDURAL"}

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._surfaced_at: dict = {}  # memory_id → datetime last surfaced

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_run is not None
                and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC):
            return []
        self._last_run = now

        try:
            from ..memory.models import MemoryType
            eligible_types = [
                t for t in MemoryType
                if t.value not in self._SKIP_TYPES
            ]
            all_mems = []
            for mt in eligible_types:
                all_mems.extend(cortex.get_by_type(mt))
        except Exception:
            return []

        candidates = [
            m for m in all_mems
            if m.activation_count >= self.ACTIVATION_THRESH
        ]
        if not candidates:
            return []

        # Sort by activation_count desc; cap at top 5 per cycle to avoid noise
        candidates.sort(key=lambda m: m.activation_count, reverse=True)
        candidates = candidates[:5]

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
                metadata={"memory_id": mem.id, "activation_count": mem.activation_count},
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
    MIN_INTERVAL_SEC = 60

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._prev_snapshot = None  # MilieuState snapshot for delta check

    def push(self, cortex) -> list[int]:
        from . import milieu as milieu_mod

        now = datetime.now()
        if (self._last_run is not None
                and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC):
            return []
        self._last_run = now

        m = milieu_mod.get()
        if m is None:
            return []  # Not yet initialized

        # Natural decay — mood drifts toward neutral without new signals
        m.tick()

        state = m.get_state()
        prev  = self._prev_snapshot

        # Decide whether to push: significant change OR extreme state
        should_push = (
            prev is None
            or m.delta(prev) > milieu_mod.PUSH_DELTA
            or state.arousal  >  0.6
            or state.valence  < -0.4
        )

        if not should_push:
            self._prev_snapshot = m.snapshot()
            return []

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=m.state_csb(),
            salience=0.4,
            urgency=0.3,   # Background context; not urgent unless NE decides it is
            ttl_seconds=600,
            metadata={"type": "milieu", "tick": state.tick},
        )
        self._prev_snapshot = m.snapshot()
        return [obs_id]


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
        self._session_fired: set = set()   # habit IDs already fired this session
        self._interval_last: dict = {}     # habit_id → datetime last fired

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_check is not None
                and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC):
            return []
        self._last_check = now

        try:
            from ..memory.models import MemoryType
            habits = cortex.get_by_type(MemoryType.PROCEDURAL)
        except Exception:
            return []

        proactive = [h for h in habits if h.metadata.get("habit_type") == "proactive"]
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
                    last is None
                    or (now - last).total_seconds() >= interval_sec
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
        if (self._last_check is not None
                and (now - self._last_check).total_seconds() < self.CHECK_INTERVAL_SEC):
            return []
        self._last_check = now

        try:
            from ..memory.models import MemoryType
            habits = cortex.get_by_type(MemoryType.PROCEDURAL)
        except Exception:
            return []

        try:
            from ..tools.filesystem import evaluate_threshold_habits, _resource_load_dict
            tripped = evaluate_threshold_habits(habits)
        except Exception:
            return []

        if not tripped:
            # Clear suppression for habits that are no longer tripping
            self._last_pushed.clear()
            return []

        pushed = []
        for item in tripped:
            habit        = item["habit"]
            current      = item["current_value"]
            field        = item["field"]
            raw          = item["raw"]
            verdict      = raw.get("verdict", "warn")
            ttl          = int(habit.metadata.get("twm_ttl_seconds", 120))
            surface_tmpl = habit.metadata.get(
                "surface_message",
                f"{field} is at {{current_value}} — check before queuing more work."
            )
            surface_msg  = surface_tmpl.format(
                current_value=current, field=field, verdict=verdict, **raw
            )

            # Suppress if we already pushed this habit at this or higher severity
            prev = self._last_pushed.get(habit.id)
            severity = {"warn": 1, "critical": 2}.get(verdict, 0)
            prev_sev  = {"warn": 1, "critical": 2}.get(prev, 0)
            if prev is not None and prev_sev >= severity:
                continue  # already notified, condition hasn't escalated

            urgency  = 0.7 if verdict == "critical" else 0.5
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

        return pushed


# ── Module singletons + convenience runner ────────────────────────────────────

memory_surfacer        = MemorySurfacer()
heartbeat_source       = HeartbeatSource()
user_input_source      = UserInputSource()
machines_watcher       = MachinesWatcher()
inbox_watcher          = InboxWatcher()
milieu_source          = MilieuSource()
habit_candidate_source = HabitCandidateSource()
proactive_habit_source = ProactiveHabitSource()
resource_monitor       = ResourceMonitorSource()


def run_background_sources(cortex) -> int:
    """
    Run all timer-based sources. Call once per main loop iteration.
    Returns total count of observations pushed this call.
    Exceptions are swallowed — a broken source must not crash the loop.
    """
    pushed = 0
    for src in (heartbeat_source, memory_surfacer, machines_watcher, inbox_watcher,
                milieu_source, habit_candidate_source, proactive_habit_source,
                resource_monitor):
        try:
            ids = src.push(cortex)
            pushed += len(ids)
        except Exception:
            pass  # FAIL = FAL
    return pushed
