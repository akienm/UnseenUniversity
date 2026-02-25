"""
Push Sources — processes that deposit observations into TWM proactively.

Three sources:
  MemorySurfacer  — surfaces relevant LTM memories into TWM as background context
  TimerSentinel   — pushes temporal heartbeats so Igor stays time-aware
  UserInputSource — wraps incoming messages as TWM observations (explicit call)

All push via cortex.twm_push(). None of them block or crash the main loop.
"""

from collections import Counter
from datetime import datetime
from typing import Optional


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


# ── TimerSentinel ─────────────────────────────────────────────────────────────

class TimerSentinel(BasePushSource):
    """
    Pushes temporal heartbeat observations into TWM.

    Gives Igor time-awareness: what time it is, how long the session
    has been running, what day it is.
    Rate-limited to MIN_INTERVAL_SEC.
    """
    name = "timer_sentinel"
    MIN_INTERVAL_SEC = 300  # At most every 5 minutes

    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._session_start: datetime = datetime.now()

    def push(self, cortex) -> list[int]:
        now = datetime.now()
        if (self._last_run is not None
                and (now - self._last_run).total_seconds() < self.MIN_INTERVAL_SEC):
            return []

        self._last_run = now
        session_mins = int((now - self._session_start).total_seconds() / 60)

        csb = (
            f"TIME|{now.strftime('%Y-%m-%dT%H:%M')}|"
            f"day={now.strftime('%A')}|"
            f"session_age={session_mins}min"
        )
        obs_id = cortex.twm_push(
            source=self.name,
            content_csb=csb,
            salience=0.2,
            metadata={"session_minutes": session_mins},
            ttl_seconds=600,
        )
        return [obs_id]


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


# ── Module singletons + convenience runner ────────────────────────────────────

memory_surfacer   = MemorySurfacer()
timer_sentinel    = TimerSentinel()
user_input_source = UserInputSource()


def run_background_sources(cortex) -> int:
    """
    Run all timer-based sources. Call once per main loop iteration.
    Returns total count of observations pushed this call.
    Exceptions are swallowed — a broken source must not crash the loop.
    """
    pushed = 0
    for src in (timer_sentinel, memory_surfacer):
        try:
            ids = src.push(cortex)
            pushed += len(ids)
        except Exception:
            pass  # FAIL = FAL
    return pushed
