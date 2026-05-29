"""Tests for devices.granny.dispatch — cc_dispatch_fn."""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest
from devices.granny.dispatch import cc_dispatch_fn


def _ticket(id="T-test-dispatch", size="S", tags=None):
    return {"id": id, "title": "test dispatch ticket", "size": size, "tags": tags or ["Platform"]}


class TestCcDispatchFn:
    def test_returns_true_on_success(self):
        with (
            patch("subprocess.run") as mock_run,
            patch("unseen_university.channel.post_to_channel") as mock_post,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = cc_dispatch_fn(_ticket())
        assert result is True

    def test_posts_granny_dispatch_to_channel(self):
        posted = []
        def capture(msg, author, channel):
            posted.append((msg, author, channel))

        with (
            patch("subprocess.run") as mock_run,
            patch("unseen_university.channel.post_to_channel", side_effect=capture),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            cc_dispatch_fn(_ticket("T-abc", tags=["Platform", "Infrastructure"]))

        assert len(posted) == 1
        msg, author, channel = posted[0]
        assert "GRANNY_DISPATCH" in msg
        assert "T-abc" in msg
        assert "worker=claude" in msg
        assert author == "granny-weatherwax"
        assert channel == "shared"

    def test_returns_false_on_missing_id(self):
        result = cc_dispatch_fn({"title": "no id"})
        assert result is False

    def test_still_posts_channel_if_queue_dispatch_fails(self):
        posted = []
        with (
            patch("subprocess.run", side_effect=Exception("queue down")),
            patch("unseen_university.channel.post_to_channel", side_effect=lambda *a, **kw: posted.append(a)),
        ):
            result = cc_dispatch_fn(_ticket())
        assert result is True
        assert len(posted) == 1
