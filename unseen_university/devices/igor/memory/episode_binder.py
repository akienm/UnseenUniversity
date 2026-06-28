"""
episode_binder.py — T-ring-to-binding

Hippocampal episode binding: groups raw ring entries into coherent episodes.

The ring is a 50-entry FIFO of isolated strings — a sticky notepad.
The episode binder wraps it, accumulating entries during a turn/exchange
and flushing them as bound episode bundles.

An episode bundle captures what happened together:
  - What was said (user input + Igor response)
  - What was happening (active habit, thread context)
  - Emotional state (milieu VAD snapshot)
  - What changed (tool calls, memory deposits, state transitions)
  - Temporal binding (timestamp range of the episode)

Episodes are stored as EPISODIC memories in the graph. Consolidation
replays these bundles (not raw ring strings) for long-term storage.

Pattern completion: a partial cue activates spreading search over
episode nodes, returning the best-matching bound episode.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase

if TYPE_CHECKING:
    from .cortex import Cortex

logger = logging.getLogger(__name__)

# Time window: entries within this many seconds of each other
# are candidates for the same episode.
EPISODE_WINDOW_SEC = 120  # 2 minutes


@dataclass
class Episode:
    """A bound episode — multiple simultaneous events grouped together."""

    episode_id: str = ""
    thread_id: Optional[str] = None
    timestamp_start: str = ""
    timestamp_end: str = ""

    # What was said
    user_input: str = ""
    igor_response: str = ""

    # What was happening
    active_habit_id: Optional[str] = None
    active_habit_name: Optional[str] = None

    # Emotional state (milieu VAD snapshot)
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.5

    # What changed (ring entries grouped by category)
    tool_calls: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)
    system_events: list[str] = field(default_factory=list)

    # Raw ring entry IDs that compose this episode
    ring_entry_ids: list[int] = field(default_factory=list)

    def to_narrative(self) -> str:
        """Compress episode into a narrative string for memory storage."""
        parts = []
        if self.user_input:
            parts.append(f"User: {self.user_input[:200]}")
        if self.igor_response:
            parts.append(f"Igor: {self.igor_response[:200]}")
        if self.active_habit_name:
            parts.append(f"Habit: {self.active_habit_name}")
        if self.tool_calls:
            parts.append(f"Tools: {', '.join(self.tool_calls[:5])}")
        if self.state_changes:
            parts.append(f"Changes: {'; '.join(self.state_changes[:3])}")
        return " | ".join(parts) if parts else "empty episode"

    def to_metadata(self) -> dict:
        """Episode metadata for memory node storage."""
        return {
            "episode_id": self.episode_id,
            "thread_id": self.thread_id,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "active_habit_id": self.active_habit_id,
            "valence": self.valence,
            "arousal": self.arousal,
            "dominance": self.dominance,
            "ring_entry_count": len(self.ring_entry_ids),
            "tool_call_count": len(self.tool_calls),
            "deposited_by": "episode_binder",
        }


class EpisodeBinder(IgorBase):
    """
    Accumulates raw ring entries and flushes bound episodes.

    Usage during a turn::

        binder = EpisodeBinder()
        binder.observe_input(user_input, thread_id)
        # ... turn executes, ring entries written ...
        binder.observe_response(response_text)
        binder.observe_milieu(valence, arousal, dominance)
        binder.observe_habit(habit_id, habit_name)
        episode = binder.flush(cortex)  # binds + deposits

    The flush reads recent ring entries since the episode started,
    groups them into the episode bundle, and optionally deposits
    the episode as an EPISODIC memory node.
    """

    def __init__(self):
        super().__init__()
        self._started_at: Optional[str] = None
        self._thread_id: Optional[str] = None
        self._user_input: str = ""
        self._response: str = ""
        self._habit_id: Optional[str] = None
        self._habit_name: Optional[str] = None
        self._valence: float = 0.0
        self._arousal: float = 0.0
        self._dominance: float = 0.5
        self._ring_snapshot_id: Optional[int] = None

    def observe_input(self, user_input: str, thread_id: Optional[str] = None) -> None:
        """Mark the start of an episode with user input."""
        self._started_at = datetime.now().isoformat()
        self._user_input = user_input
        self._thread_id = thread_id

    def observe_response(self, response: str) -> None:
        """Record Igor's response for this episode."""
        self._response = response

    def observe_milieu(self, valence: float, arousal: float, dominance: float) -> None:
        """Snapshot the milieu state during this episode."""
        self._valence = valence
        self._arousal = arousal
        self._dominance = dominance

    def observe_habit(
        self, habit_id: Optional[str], habit_name: Optional[str] = None
    ) -> None:
        """Record which habit fired during this episode."""
        self._habit_id = habit_id
        self._habit_name = habit_name

    def snapshot_ring_position(self, cortex: "Cortex") -> None:
        """
        Record the current ring position so flush() knows which entries
        are new since the episode started.
        """
        try:
            entries = cortex.read_ring_memory(limit=1)
            if entries:
                self._ring_snapshot_id = entries[-1]["id"]
            else:
                self._ring_snapshot_id = 0
        except Exception:
            self._ring_snapshot_id = 0

    def flush(self, cortex: "Cortex", deposit: bool = True) -> Optional[Episode]:
        """
        Bind accumulated observations into an episode.

        Reads ring entries written since snapshot_ring_position() was called,
        categorizes them, and builds the Episode bundle.

        If deposit=True, stores the episode as an EPISODIC memory node.

        Returns the bound Episode, or None if nothing to bind.
        """
        if not self._started_at and not self._user_input:
            return None

        now = datetime.now().isoformat()

        # Read ring entries since snapshot
        ring_entries = self._read_new_ring_entries(cortex)

        # Categorize ring entries
        tool_calls = []
        state_changes = []
        system_events = []
        ring_ids = []

        for entry in ring_entries:
            content = entry.get("content", "")
            category = entry.get("category", "")
            ring_ids.append(entry.get("id", 0))

            if "TOOL_RESULT" in content or "RESOLVED" in content:
                tool_calls.append(content[:150])
            elif category in (
                "habit_trace",
                "impulse_executed",
                "gate_trace",
            ):
                state_changes.append(content[:150])
            elif category in (
                "session_control",
                "system_info",
                "consolidation",
            ):
                system_events.append(content[:150])
            elif "HABIT" in content or "GATE" in content:
                state_changes.append(content[:150])
            else:
                system_events.append(content[:150])

        episode_id = f"EP_{self._started_at.replace('-', '').replace(':', '').replace('.', '_')[:15]}"

        episode = Episode(
            episode_id=episode_id,
            thread_id=self._thread_id,
            timestamp_start=self._started_at or now,
            timestamp_end=now,
            user_input=self._user_input,
            igor_response=self._response,
            active_habit_id=self._habit_id,
            active_habit_name=self._habit_name,
            valence=self._valence,
            arousal=self._arousal,
            dominance=self._dominance,
            tool_calls=tool_calls,
            state_changes=state_changes,
            system_events=system_events,
            ring_entry_ids=ring_ids,
        )

        if deposit:
            self._deposit_episode(cortex, episode)

        # Reset for next episode
        self._reset()

        return episode

    def _read_new_ring_entries(self, cortex: "Cortex") -> list[dict]:
        """Read ring entries written since the snapshot position."""
        try:
            all_entries = cortex.read_ring_memory(
                limit=RING_MAX_READ, thread_id=self._thread_id
            )
        except Exception:
            return []

        if self._ring_snapshot_id is None:
            return all_entries

        # Filter to entries newer than snapshot
        return [e for e in all_entries if e.get("id", 0) > self._ring_snapshot_id]

    def _deposit_episode(self, cortex: "Cortex", episode: Episode) -> None:
        """Store the episode as an EPISODIC memory node in the graph."""
        try:
            from .models import Memory, MemoryType

            mem = Memory(
                id=episode.episode_id,
                narrative=episode.to_narrative(),
                memory_type=MemoryType.EPISODIC,
                metadata=episode.to_metadata(),
                valence=episode.valence,
                arousal=episode.arousal,
                dominance=episode.dominance,
                source="episode_binder",
                certainty=0.9,
            )
            cortex.store(mem)
            logger.debug("Deposited episode %s", episode.episode_id)
        except Exception as exc:
            logger.warning("Failed to deposit episode %s: %s", episode.episode_id, exc)

    def _reset(self) -> None:
        """Reset accumulator for next episode."""
        self._started_at = None
        self._thread_id = None
        self._user_input = ""
        self._response = ""
        self._habit_id = None
        self._habit_name = None
        self._valence = 0.0
        self._arousal = 0.0
        self._dominance = 0.5
        self._ring_snapshot_id = None


# Max ring entries to read when building an episode
RING_MAX_READ = 50


def replay_episodes(
    cortex: "Cortex", since_hours: int = 24, limit: int = 20
) -> list[dict]:
    """
    Replay recent episodes for consolidation.

    Returns episode memory nodes deposited by the binder,
    ordered oldest-first for sequential replay.
    """
    try:
        results = cortex.search(
            "episode_binder",
            limit=limit,
            memory_types=["EPISODIC"],
        )
        # Filter to recent episodes
        cutoff = datetime.now()
        episodes = []
        for mem in results:
            meta = mem.metadata or {}
            if meta.get("deposited_by") != "episode_binder":
                continue
            ts = meta.get("timestamp_start", "")
            if ts:
                try:
                    ep_time = datetime.fromisoformat(ts)
                    hours_ago = (cutoff - ep_time).total_seconds() / 3600
                    if hours_ago > since_hours:
                        continue
                except (ValueError, TypeError) as e:
                    logger.debug("query_episodes: fromisoformat failed: %s", e)
            episodes.append(
                {
                    "id": mem.id,
                    "narrative": mem.narrative,
                    "metadata": meta,
                    "valence": mem.valence,
                    "arousal": mem.arousal,
                }
            )
        return episodes
    except Exception as exc:
        logger.warning("replay_episodes failed: %s", exc)
        return []


def complete_episode(cortex: "Cortex", cue: str, limit: int = 5) -> list[dict]:
    """
    Pattern completion: partial cue reconstructs matching episodes.

    Searches episode nodes by cue text and returns the best matches
    with their full bound context.
    """
    try:
        results = cortex.search(
            cue,
            limit=limit,
            memory_types=["EPISODIC"],
        )
        episodes = []
        for mem in results:
            meta = mem.metadata or {}
            if meta.get("deposited_by") != "episode_binder":
                continue
            episodes.append(
                {
                    "id": mem.id,
                    "narrative": mem.narrative,
                    "metadata": meta,
                    "valence": mem.valence,
                    "arousal": mem.arousal,
                }
            )
        return episodes
    except Exception as exc:
        logger.warning("complete_episode failed: %s", exc)
        return []
