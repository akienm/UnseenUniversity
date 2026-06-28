"""
test_episode_binder.py — T-ring-to-binding

Tests for hippocampal episode binding.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Recent timestamps so 48-hour replay filter doesn't exclude the fixtures.
_NOW = datetime.now(timezone.utc)
_RECENT_TS = (_NOW - timedelta(hours=1)).isoformat()
_RECENT_TS_PLUS_1S = (_NOW - timedelta(hours=1) + timedelta(seconds=1)).isoformat()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_cortex(ring_entries=None):
    """Create a mock cortex with ring memory support."""
    cortex = MagicMock()
    cortex.read_ring_memory.return_value = ring_entries or []
    cortex.twm_push.return_value = 1
    cortex.store.return_value = None
    return cortex


class TestEpisode:
    def test_to_narrative(self):
        from unseen_university.devices.igor.memory.episode_binder import Episode

        ep = Episode(
            user_input="What's the weather?",
            igor_response="It's sunny today.",
            active_habit_name="weather_check",
            tool_calls=["weather_api"],
        )
        narrative = ep.to_narrative()
        assert "User: What's the weather?" in narrative
        assert "Igor: It's sunny" in narrative
        assert "Habit: weather_check" in narrative
        assert "Tools: weather_api" in narrative

    def test_to_narrative_empty(self):
        from unseen_university.devices.igor.memory.episode_binder import Episode

        ep = Episode()
        assert ep.to_narrative() == "empty episode"

    def test_to_metadata(self):
        from unseen_university.devices.igor.memory.episode_binder import Episode

        ep = Episode(
            episode_id="EP_20260418T1200",
            thread_id="discord:123",
            valence=0.5,
            arousal=0.7,
            dominance=0.6,
            ring_entry_ids=[1, 2, 3],
            tool_calls=["tool_a", "tool_b"],
        )
        meta = ep.to_metadata()
        assert meta["episode_id"] == "EP_20260418T1200"
        assert meta["thread_id"] == "discord:123"
        assert meta["ring_entry_count"] == 3
        assert meta["tool_call_count"] == 2
        assert meta["deposited_by"] == "episode_binder"


class TestEpisodeBinder:
    def test_basic_flush(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex(
            ring_entries=[
                {
                    "id": 10,
                    "category": "user_turn",
                    "content": "hello",
                    "timestamp": _RECENT_TS,
                    "thread_id": None,
                },
                {
                    "id": 11,
                    "category": "habit_trace",
                    "content": "HABIT_FIRED|id=greeting",
                    "timestamp": _RECENT_TS_PLUS_1S,
                    "thread_id": None,
                },
            ]
        )

        binder = EpisodeBinder()
        binder._ring_snapshot_id = 9  # everything after 9 is new
        binder.observe_input("hello world", "discord:123")
        binder.observe_response("Hi there!")
        binder.observe_milieu(0.5, 0.6, 0.5)
        binder.observe_habit("HABIT_GREET", "greeting")

        episode = binder.flush(cortex, deposit=False)
        assert episode is not None
        assert episode.user_input == "hello world"
        assert episode.igor_response == "Hi there!"
        assert episode.thread_id == "discord:123"
        assert episode.active_habit_id == "HABIT_GREET"
        assert episode.valence == 0.5
        assert episode.arousal == 0.6
        assert len(episode.ring_entry_ids) == 2
        assert len(episode.state_changes) == 1  # habit_trace entry

    def test_flush_deposits_to_cortex(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex(ring_entries=[])

        binder = EpisodeBinder()
        binder.observe_input("test input")
        binder.observe_response("test response")

        episode = binder.flush(cortex, deposit=True)
        assert episode is not None
        cortex.store.assert_called_once()
        stored_mem = cortex.store.call_args[0][0]
        assert stored_mem.memory_type.name == "EPISODIC"
        assert stored_mem.source == "episode_binder"
        assert "episode_binder" in stored_mem.metadata.get("deposited_by", "")

    def test_flush_resets_state(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex()
        binder = EpisodeBinder()
        binder.observe_input("test")
        binder.flush(cortex, deposit=False)

        # After flush, state should be reset
        assert binder._user_input == ""
        assert binder._response == ""
        assert binder._started_at is None

    def test_flush_with_no_input_returns_none(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex()
        binder = EpisodeBinder()
        episode = binder.flush(cortex)
        assert episode is None

    def test_snapshot_ring_position(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex(
            ring_entries=[
                {"id": 42, "category": "note", "content": "x", "timestamp": "t"}
            ]
        )
        binder = EpisodeBinder()
        binder.snapshot_ring_position(cortex)
        assert binder._ring_snapshot_id == 42

    def test_snapshot_empty_ring(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex(ring_entries=[])
        binder = EpisodeBinder()
        binder.snapshot_ring_position(cortex)
        assert binder._ring_snapshot_id == 0

    def test_categorizes_tool_results(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex(
            ring_entries=[
                {
                    "id": 20,
                    "category": "system_info",
                    "content": "RESOLVED|weather_check|sunny",
                    "timestamp": "t",
                    "thread_id": None,
                },
                {
                    "id": 21,
                    "category": "system_info",
                    "content": "TOOL_RESULT|api_call|200",
                    "timestamp": "t",
                    "thread_id": None,
                },
            ]
        )

        binder = EpisodeBinder()
        binder._ring_snapshot_id = 19
        binder.observe_input("check weather")

        episode = binder.flush(cortex, deposit=False)
        assert len(episode.tool_calls) == 2
        assert any("RESOLVED" in t for t in episode.tool_calls)
        assert any("TOOL_RESULT" in t for t in episode.tool_calls)

    def test_episode_id_format(self):
        from unseen_university.devices.igor.memory.episode_binder import EpisodeBinder

        cortex = _make_cortex()
        binder = EpisodeBinder()
        binder.observe_input("test")
        episode = binder.flush(cortex, deposit=False)
        assert episode.episode_id.startswith("EP_")


class TestReplayEpisodes:
    def test_replay_filters_by_depositor(self):
        from unseen_university.devices.igor.memory.episode_binder import replay_episodes

        mock_mem_ours = MagicMock()
        mock_mem_ours.id = "EP_1"
        mock_mem_ours.narrative = "User: hello | Igor: hi"
        mock_mem_ours.metadata = {
            "deposited_by": "episode_binder",
            "timestamp_start": _RECENT_TS,
        }
        mock_mem_ours.valence = 0.5
        mock_mem_ours.arousal = 0.3

        mock_mem_other = MagicMock()
        mock_mem_other.id = "EP_OTHER"
        mock_mem_other.narrative = "Something else"
        mock_mem_other.metadata = {"deposited_by": "manual"}
        mock_mem_other.valence = 0.0
        mock_mem_other.arousal = 0.0

        cortex = _make_cortex()
        cortex.search.return_value = [mock_mem_ours, mock_mem_other]

        episodes = replay_episodes(cortex, since_hours=48)
        assert len(episodes) == 1
        assert episodes[0]["id"] == "EP_1"


class TestCompleteEpisode:
    def test_finds_matching_episodes(self):
        from unseen_university.devices.igor.memory.episode_binder import complete_episode

        mock_mem = MagicMock()
        mock_mem.id = "EP_CALVING"
        mock_mem.narrative = "User: tell me about calving | Igor: calving is..."
        mock_mem.metadata = {"deposited_by": "episode_binder"}
        mock_mem.valence = 0.3
        mock_mem.arousal = 0.5

        cortex = _make_cortex()
        cortex.search.return_value = [mock_mem]

        results = complete_episode(cortex, "calving")
        assert len(results) == 1
        assert results[0]["id"] == "EP_CALVING"

    def test_handles_search_failure(self):
        from unseen_university.devices.igor.memory.episode_binder import complete_episode

        cortex = _make_cortex()
        cortex.search.side_effect = RuntimeError("db error")

        results = complete_episode(cortex, "test")
        assert results == []
