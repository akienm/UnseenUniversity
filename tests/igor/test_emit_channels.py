"""
test_emit_channels.py — Tests for emit_channels.py (D260, D295).

Tests for EmitChannel implementations including the new MemoryChannel.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from wild_igor.igor.cognition.emit_channels import (
    get_registry,
    BasketChannel,
    EmotionalMilieuChannel,
    CognitiveMilieuChannel,
    ConsoleChannel,
    WebChannel,
    MemoryChannel,
)
from wild_igor.igor.memory.models import Memory, MemoryType


class TestBasketChannel:
    """Tests for BasketChannel."""

    def test_basket_channel_writes_value(self):
        """BasketChannel should write value directly into basket."""
        channel = BasketChannel()
        basket = {}
        channel.write("key1", "value1", basket)

        assert basket["key1"] == "value1"

    def test_basket_channel_overwrites(self):
        """BasketChannel should overwrite existing values."""
        channel = BasketChannel()
        basket = {"key1": "old_value"}
        channel.write("key1", "new_value", basket)

        assert basket["key1"] == "new_value"

    def test_basket_channel_bidirectional(self):
        """BasketChannel should have bidirectional flag."""
        channel = BasketChannel()
        assert channel.bidirectional is True


class TestMemoryChannel:
    """Tests for MemoryChannel (D295)."""

    def test_memory_channel_stores_episodic(self):
        """MemoryChannel should store EPISODIC memory when cortex is present."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}

        channel.write("EPISODIC", "Test narrative content", basket)

        # Verify cortex.store was called with a Memory object
        assert mock_cortex.store.called
        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert isinstance(memory, Memory)
        assert memory.memory_type == MemoryType.EPISODIC
        assert memory.narrative == "Test narrative content"
        assert memory.source == "engram"

    def test_memory_channel_stores_procedural(self):
        """MemoryChannel should handle different memory types."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}

        channel.write("PROCEDURAL", "How to do X", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.memory_type == MemoryType.PROCEDURAL
        assert memory.narrative == "How to do X"

    def test_memory_channel_uses_basket_tags(self):
        """MemoryChannel should read _mem_tags from basket."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {
            "_cortex": mock_cortex,
            "_mem_tags": ["important", "work"],
        }

        channel.write("FACTUAL", "Some fact", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.metadata["tags"] == ["important", "work"]

    def test_memory_channel_uses_identity_weight(self):
        """MemoryChannel should read _mem_identity_weight from basket."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {
            "_cortex": mock_cortex,
            "_mem_identity_weight": 0.8,
        }

        channel.write("INTERPRETIVE", "My belief", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.metadata["identity_weight"] == 0.8

    def test_memory_channel_uses_salience(self):
        """MemoryChannel should read _mem_salience from basket as arousal."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {
            "_cortex": mock_cortex,
            "_mem_salience": 0.7,
        }

        channel.write("EPISODIC", "Important event", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.arousal == 0.7

    def test_memory_channel_defaults_tags(self):
        """MemoryChannel should default _mem_tags to empty list."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}  # No _mem_tags

        channel.write("EPISODIC", "Content", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.metadata["tags"] == []

    def test_memory_channel_defaults_identity_weight(self):
        """MemoryChannel should default _mem_identity_weight to 0.5."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}  # No _mem_identity_weight

        channel.write("EPISODIC", "Content", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.metadata["identity_weight"] == 0.5

    def test_memory_channel_defaults_salience(self):
        """MemoryChannel should default _mem_salience to 0.5."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}  # No _mem_salience

        channel.write("EPISODIC", "Content", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.arousal == 0.5

    def test_memory_channel_warns_no_cortex(self):
        """MemoryChannel should warn and skip if _cortex is missing."""
        channel = MemoryChannel()
        basket = {}  # No _cortex

        # Should not raise, should just log warning
        with patch("wild_igor.igor.cognition.emit_channels.log") as mock_log:
            channel.write("EPISODIC", "Content", basket)
            mock_log.warning.assert_called()

    def test_memory_channel_warns_invalid_type(self):
        """MemoryChannel should warn if memory type is invalid."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}

        with patch("wild_igor.igor.cognition.emit_channels.log") as mock_log:
            channel.write("INVALID_TYPE", "Content", basket)
            mock_log.warning.assert_called()
            mock_cortex.store.assert_not_called()

    def test_memory_channel_sets_source_and_confidence(self):
        """MemoryChannel should set source='engram' and confidence=0.7."""
        channel = MemoryChannel()
        mock_cortex = Mock()
        basket = {"_cortex": mock_cortex}

        channel.write("EPISODIC", "Content", basket)

        call_args = mock_cortex.store.call_args
        memory = call_args[0][0]

        assert memory.source == "engram"
        assert memory.confidence == 0.7

    def test_memory_channel_non_bidirectional(self):
        """MemoryChannel should not be bidirectional."""
        channel = MemoryChannel()
        assert channel.bidirectional is False


class TestEmitChannelRegistry:
    """Tests for EmitChannelRegistry."""

    def test_registry_has_memory_channel(self):
        """Registry should include MemoryChannel."""
        registry = get_registry()
        channel_names = registry.names()

        assert "memory" in channel_names

    def test_registry_has_all_channels(self):
        """Registry should include all expected channels."""
        registry = get_registry()
        channel_names = registry.names()

        expected = [
            "basket",
            "emotional_milieu",
            "cognitive_milieu",
            "console",
            "web",
            "discord",
            "memory",
        ]

        for name in expected:
            assert name in channel_names, f"Missing channel: {name}"

    def test_registry_write_delegates(self):
        """Registry.write should delegate to appropriate channel."""
        registry = get_registry()
        basket = {}

        registry.write("basket", "key1", "value1", basket)

        assert basket["key1"] == "value1"

    def test_registry_write_unknown_channel_warns(self):
        """Registry.write should warn for unknown channel."""
        registry = get_registry()
        basket = {}

        with patch("wild_igor.igor.cognition.emit_channels.log") as mock_log:
            registry.write("nonexistent_channel", "key", "value", basket)
            mock_log.warning.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
