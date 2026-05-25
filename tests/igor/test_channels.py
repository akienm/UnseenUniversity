"""
test_channels.py — Channel registry and acquisition tests.

Tests for D230/D231 acquisition channel framework.
T-igor-channels-relocate: channels relocated to wild_igor/igor/acquisition/.
"""

import json
import tempfile
from pathlib import Path

import pytest

from wild_igor.igor.acquisition import (
    AcquireRequest,
    ChannelFailure,
    AcquireResult,
    ChannelReliability,
    get_registry,
)
from wild_igor.igor.acquisition.file_inbox import FileInboxChannel
from wild_igor.igor.acquisition.direct_url import DirectURLChannel


class TestAcquireRequest:
    """Test AcquireRequest data structure."""

    def test_minimal_request(self):
        """AcquireRequest with only query."""
        req = AcquireRequest(query="test search")
        assert req.query == "test search"
        assert req.context == {}

    def test_request_with_context(self):
        """AcquireRequest with context metadata."""
        req = AcquireRequest(query="test", context={"source": "habit"})
        assert req.query == "test"
        assert req.context["source"] == "habit"


class TestChannelFailure:
    """Test ChannelFailure result."""

    def test_failure_minimal(self):
        """Minimal failure."""
        failure = ChannelFailure(
            channel_name="TestChannel",
            reason="No matches found",
        )
        assert failure.channel_name == "TestChannel"
        assert failure.reason == "No matches found"
        assert failure.cost_usd == 0.0
        assert failure.retry_in_seconds is None

    def test_failure_with_retry(self):
        """Failure with retry suggestion."""
        failure = ChannelFailure(
            channel_name="TestChannel",
            reason="Rate limited",
            retry_in_seconds=5.0,
        )
        assert failure.retry_in_seconds == 5.0


class TestAcquireResult:
    """Test successful AcquireResult."""

    def test_result_creation(self):
        """Create an AcquireResult."""
        from wild_igor.igor.acquisition import BlobMeta

        blob = b"test content"
        meta = BlobMeta(
            title="Test",
            source="TestChannel",
            format="text",
            size_bytes=len(blob),
        )
        result = AcquireResult(blob=blob, meta=meta, cost_usd=0.0)

        assert result.blob == blob
        assert result.meta.title == "Test"
        assert result.cost_usd == 0.0


class TestChannelRegistry:
    """Test ChannelRegistry."""

    def test_registry_empty_at_start(self):
        """Registry should support registration."""
        # Create a fresh registry (not the global one)
        from wild_igor.igor.acquisition import ChannelRegistry

        registry = ChannelRegistry()
        assert len(registry.list_channels()) == 0

    def test_register_channel(self):
        """Register a channel."""
        from wild_igor.igor.acquisition import ChannelRegistry, Channel

        registry = ChannelRegistry()

        class DummyChannel(Channel):
            def acquire(self, request):
                return ChannelFailure(channel_name=self.name, reason="Not implemented")

        ch = DummyChannel(
            name="Dummy",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
        )
        registry.register(ch)

        assert len(registry.list_channels()) == 1
        assert registry.get("Dummy") == ch

    def test_duplicate_registration_fails(self):
        """Registering same channel name twice should fail."""
        from wild_igor.igor.acquisition import ChannelRegistry, Channel

        registry = ChannelRegistry()

        class DummyChannel(Channel):
            def acquire(self, request):
                return ChannelFailure(channel_name=self.name, reason="Not implemented")

        ch1 = DummyChannel(
            name="Dummy",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
        )
        ch2 = DummyChannel(
            name="Dummy",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
        )

        registry.register(ch1)
        with pytest.raises(ValueError):
            registry.register(ch2)


class TestFileInboxChannel:
    """Test FileInboxChannel."""

    def test_empty_query_fails(self):
        """Empty query should fail."""
        channel = FileInboxChannel()
        req = AcquireRequest(query="")
        result = channel.acquire(req)

        assert isinstance(result, ChannelFailure)
        assert "Empty" in result.reason

    def test_nonexistent_file_fails(self):
        """Query for nonexistent file should fail."""
        channel = FileInboxChannel()
        req = AcquireRequest(query="nonexistent_file_xyz_12345")
        result = channel.acquire(req)

        assert isinstance(result, ChannelFailure)
        assert "No files" in result.reason or "nonexistent" in result.reason.lower()

    def test_file_acquisition(self):
        """Test acquiring a file from inbox."""
        # Create a temporary inbox directory
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            # Monkey-patch paths().inbox to point to our temp dir
            from wild_igor.igor import paths as paths_module

            original_inbox = paths_module._BootstrapPathManager.inbox.fget

            def mock_inbox(self):
                return Path(tmpdir)

            paths_module._BootstrapPathManager.inbox = property(mock_inbox)

            try:
                # Create a test file in the inbox
                test_file = Path(tmpdir) / "test_document.txt"
                test_content = b"This is test content for acquisition."
                test_file.write_bytes(test_content)

                # Acquire it
                channel = FileInboxChannel()
                req = AcquireRequest(query="test_document")
                result = channel.acquire(req)

                assert isinstance(result, AcquireResult)
                assert result.blob == test_content
                assert result.meta.title == "test_document"
                assert result.meta.format == "text"
            finally:
                # Restore original
                paths_module._BootstrapPathManager.inbox = property(original_inbox)


class TestDirectURLChannel:
    """Test DirectURLChannel."""

    def test_empty_query_fails(self):
        """Empty query should fail."""
        channel = DirectURLChannel()
        req = AcquireRequest(query="")
        result = channel.acquire(req)

        assert isinstance(result, ChannelFailure)
        assert "Empty" in result.reason

    def test_file_path_acquisition(self):
        """Test acquiring from a file path."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"File content")
            tmp.flush()
            tmp_path = tmp.name

        try:
            channel = DirectURLChannel()
            req = AcquireRequest(query=tmp_path)
            result = channel.acquire(req)

            assert isinstance(result, AcquireResult)
            assert result.blob == b"File content"
            assert result.meta.file_path == tmp_path
        finally:
            Path(tmp_path).unlink()

    def test_nonexistent_file_fails(self):
        """Nonexistent file should fail."""
        channel = DirectURLChannel()
        req = AcquireRequest(query="/nonexistent/path/xyz123.txt")
        result = channel.acquire(req)

        assert isinstance(result, ChannelFailure)
        assert "not found" in result.reason.lower() or "error" in result.reason.lower()


class TestChannelIntegration:
    """Integration tests for the registry."""

    def test_global_registry_bootstrapped(self):
        """Global registry should be auto-bootstrapped with all channels."""
        registry = get_registry()
        channels = registry.list_channels()

        # Should have at least the basic channels
        channel_names = [ch.name for ch in channels]
        assert "FileInboxChannel" in channel_names
        assert "DirectURLChannel" in channel_names

    def test_registry_order(self):
        """Channels should be in priority order (D231)."""
        registry = get_registry()
        channels = registry.list_channels()
        channel_names = [ch.name for ch in channels]

        # Expected order (D231)
        expected_order = [
            "FileInboxChannel",
            "DirectURLChannel",
            "CalibreChannel",
            "GeminiSearchChannel",
            "BrowserUseChannel",
        ]

        for i, name in enumerate(expected_order):
            if name in channel_names:
                assert channel_names.index(name) == i, f"{name} not in correct position"

    def test_short_circuit_flags(self):
        """FileInbox and DirectURL should short-circuit."""
        registry = get_registry()
        channels_by_name = {ch.name: ch for ch in registry.list_channels()}

        inbox = channels_by_name.get("FileInboxChannel")
        if inbox:
            assert inbox.short_circuits is True

        direct = channels_by_name.get("DirectURLChannel")
        if direct:
            assert direct.short_circuits is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
