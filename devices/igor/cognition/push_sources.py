"""
Push Sources — processes that deposit observations into TWM proactively.

Sources:
  HeartbeatSource — Igor's anterior cingulate on a clock (change.31).
                    Replaces TimerSentinel. Checks time, budget, HEARTBEAT
                    procedural memories, and fires proactive Discord alerts.
  MemorySurfacer  — surfaces relevant LTM memories into TWM as background context
  UserInputSource — wraps incoming messages as TWM observations (explicit call)
  MachinesWatcher — watches machines.csv for cluster state changes
  InboxWatcher    — watches inbox directory for new files (5s)

All push via cortex.twm_push(). None of them block or crash the main loop.
"""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

MACHINES_CSV = Path.home() / ".TheIgors" / "local" / "machines.csv"
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
    """
    name = "memory_surfacer"
    MIN_INTERVAL_SEC = 120  # At most every 2 minutes

    _STOP = {
        "from", "that", "with", "this", "have", "been", "will", "were",
        "they", "what", "when", "where", "which", "there", "their",
        "about", "could", "would", "should", "intent", "friction",
        "igor", "user", "akien",
    }

    def __init__(self):
        self._last_run: Optional[datetime] = None

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
        words = [w.lower() for w in combined.split() if len(w) > 4]
        keywords = [w for w in words if w not in self._STOP]
        if not keywords:
            return []

        top_terms = " ".join(w for w, _ in Counter(keywords).most_common(5))
        candidates = cortex.search(top_terms, limit=5)
        if not candidates:
            return []

        pushed = []
        for mem in candidates:
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
            )
            pushed.append(obs_id)

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
      5. TODO change.33: check arbiter pending items.
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
        )
        pushed.append(obs_id)

        # 2. Budget status check
        pushed.extend(self._check_budget(cortex))

        # 3. HEARTBEAT procedural memories (user-defined conditions)
        pushed.extend(self._check_heartbeat_memories(cortex, now))

        # TODO change.33: check arbiter pending items and push as high-salience obs

        return pushed

    def _check_budget(self, cortex) -> list[int]:
        """Push high-salience budget alert if warn/critical. Fire Discord once per level."""
        try:
            from ..tools.budget import budget_status
            s = budget_status()
        except Exception:
            return []

        remaining = s["remaining_usd"]
        if remaining > s["budget_usd"] * 0.20 and not s["critical"]:
            return []  # Budget fine — stay quiet

        if remaining <= 0:
            level, salience = "EXHAUSTED", 1.0
            msg = (f"Budget EXHAUSTED: spent ${s['spent_usd']:.2f} of "
                   f"${s['budget_usd']:.2f}. Claude calls blocked.")
        elif s["critical"]:
            level, salience = "CRITICAL", 0.9
            msg = (f"Budget CRITICAL: ${remaining:.2f} remaining of "
                   f"${s['budget_usd']:.2f}.")
        else:
            level, salience = "LOW", 0.7
            msg = (f"Budget LOW: ${remaining:.2f} remaining "
                   f"({100 - s['pct_used']:.0f}% left).")

        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=f"BUDGET_{level}|{msg}",
            salience=salience,
            metadata={"level": level, "remaining_usd": remaining},
            ttl_seconds=600,
        )

        # Proactive Discord ping for CRITICAL/EXHAUSTED (once per session per level)
        if level in ("CRITICAL", "EXHAUSTED") and level not in self._discord_alerted:
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
        """Push a user/network message into TWM. Returns obs ID."""
        csb = f"MSG|ch={channel}|from={author}|{content[:300]}"
        obs_id = cortex.twm_push(
            source=f"{self.name}:{channel}",
            content_csb=csb,
            salience=0.7,
            metadata={"channel": channel, "author": author},
            ttl_seconds=1800,  # messages stay relevant for 30 min
        )
        return obs_id


# ── MachinesWatcher ───────────────────────────────────────────────────────────

class MachinesWatcher(BasePushSource):
    """
    Watches ~/.TheIgors/local/machines.csv for changes.

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

        if not MACHINES_CSV.exists():
            return []

        try:
            current_mtime = MACHINES_CSV.stat().st_mtime
        except OSError:
            return []

        first_run = self._last_mtime is None
        changed = first_run or (current_mtime != self._last_mtime)
        self._last_mtime = current_mtime

        if not changed:
            return []

        try:
            csv_text = MACHINES_CSV.read_text(encoding="utf-8").strip()
        except OSError:
            return []

        reason = "initial_load" if first_run else "file_changed"
        csb = f"MACHINES_CSV|{reason}|{now.strftime('%Y-%m-%dT%H:%M')}|{csv_text[:600]}"
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.8,
            metadata={"path": str(MACHINES_CSV), "mtime": current_mtime, "reason": reason},
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
            )
            pushed.append(obs_id)

        return pushed


# ── Module singletons + convenience runner ────────────────────────────────────

memory_surfacer   = MemorySurfacer()
heartbeat_source  = HeartbeatSource()
user_input_source = UserInputSource()
machines_watcher  = MachinesWatcher()
inbox_watcher     = InboxWatcher()


def run_background_sources(cortex) -> int:
    """
    Run all timer-based sources. Call once per main loop iteration.
    Returns total count of observations pushed this call.
    Exceptions are swallowed — a broken source must not crash the loop.
    """
    pushed = 0
    for src in (heartbeat_source, memory_surfacer, machines_watcher, inbox_watcher):
        try:
            ids = src.push(cortex)
            pushed += len(ids)
        except Exception:
            pass  # FAIL = FAL
    return pushed
