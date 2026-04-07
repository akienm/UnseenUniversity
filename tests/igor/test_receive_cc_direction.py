"""Tests for receive_cc_direction tool."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_cortex():
    cortex = MagicMock()
    cortex.twm_push.return_value = 1
    stored = []

    def _store(mem):
        mem.id = "TEST1234"
        stored.append(mem)

    cortex.store.side_effect = _store
    cortex._stored = stored
    return cortex


@pytest.fixture(autouse=True)
def _patch_cortex(monkeypatch):
    """Patch get_cortex so tests don't need a live DB."""
    cortex = _make_cortex()
    monkeypatch.setattr(
        "wild_igor.igor.tools.receive_cc_direction._get_cortex",
        lambda: cortex,
    )
    return cortex


def test_empty_content_skipped():
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    result = receive_cc_direction("")
    assert "skipped" in result.lower()


def test_cc_prefix_stripped():
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    with patch("wild_igor.igor.tools.channel_post.post_to_channel"):
        result = receive_cc_direction(
            "CC: we're changing direction toward local inference"
        )
    assert "stored" in result.lower()


def test_deposits_factual_with_identity_weight(_patch_cortex):
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    with patch("wild_igor.igor.tools.channel_post.post_to_channel"):
        receive_cc_direction("we are moving toward Igor as Claude Code from now on")

    assert len(_patch_cortex._stored) == 1
    mem = _patch_cortex._stored[0]
    assert mem.memory_type.value == "FACTUAL"
    assert mem.metadata.get("identity_weight") == 0.9
    assert mem.metadata.get("source") == "claude-code"


def test_twm_push_called(_patch_cortex):
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    with patch("wild_igor.igor.tools.channel_post.post_to_channel"):
        receive_cc_direction("new direction: D316 progressive autonomy path decided")

    _patch_cortex.twm_push.assert_called_once()
    call_kwargs = _patch_cortex.twm_push.call_args
    assert call_kwargs.kwargs.get("ttl_seconds") == 6 * 3600
    assert call_kwargs.kwargs.get("salience", 0) >= 0.8


def test_channel_post_called():
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    with patch("wild_igor.igor.tools.channel_post.post_to_channel") as mock_post:
        receive_cc_direction("priority is now to reduce Igor's OR spend")

    mock_post.assert_called_once()
    msg = mock_post.call_args[0][0]
    assert "DIRECTION RECEIVED" in msg


def test_channel_post_failure_does_not_crash(_patch_cortex):
    from wild_igor.igor.tools.receive_cc_direction import receive_cc_direction

    with patch(
        "wild_igor.igor.tools.channel_post.post_to_channel",
        side_effect=RuntimeError("channel down"),
    ):
        result = receive_cc_direction("going forward, focus on strategic routing")

    assert "stored" in result.lower()
