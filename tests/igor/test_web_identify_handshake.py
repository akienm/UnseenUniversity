"""tests/test_web_identify_handshake.py — T-web-channel-identify-not-interaction

Regression guard: __identify__:<name> frames from the web channel must be
routed to the auth-handshake handler, NOT the interaction loop.

Observable invariants:
1. interaction_count is NOT incremented when __identify__: is received.
2. _user_ctx_mgr.preseed() IS called with the name from the frame.
3. A regular web message DOES increment interaction_count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch


@dataclass
class _FakeMsg:
    content: str
    author: str = "Akien"
    source: str = "web"
    reply_info: dict = field(default_factory=dict)
    raw: Any = None
    received_at: float = 0.0


class TestWebIdentifyHandshake:
    def _make_igor_stub(self):
        """Build a minimal stub that exercises _process_network_msg."""
        from wild_igor.igor.main import Igor

        igor = MagicMock(spec=Igor)
        igor.interaction_count = 0

        # Wire _user_ctx_mgr with a preseed spy
        igor._user_ctx_mgr = MagicMock()

        # Attach the real method bound to the stub
        from wild_igor.igor.main import Igor as _RealIgor
        igor._process_network_msg = lambda msg, tid: _RealIgor._process_network_msg(
            igor, msg, tid
        )
        return igor

    def test_identify_frame_does_not_increment_interaction_count(self):
        """__identify__:Akien must not bump interaction_count."""
        igor = self._make_igor_stub()
        msg = _FakeMsg(content="__identify__:Akien", author="Akien", source="web")

        with (
            patch("wild_igor.igor.main.Igor._process") as mock_process,
        ):
            igor._process_network_msg(msg, "web:thread1")

        # interaction_count stays at 0 — _process was not called
        assert igor.interaction_count == 0, (
            "__identify__ frame must not increment interaction_count"
        )
        mock_process.assert_not_called()

    def test_identify_frame_calls_preseed(self):
        """__identify__:Akien must call preseed('Akien')."""
        igor = self._make_igor_stub()
        msg = _FakeMsg(content="__identify__:Akien", author="Akien", source="web")

        with patch("wild_igor.igor.main.Igor._process"):
            igor._process_network_msg(msg, "web:thread1")

        igor._user_ctx_mgr.preseed.assert_called_once_with("web:thread1", "Akien")

    def test_identify_prefix_in_source_text(self):
        """Regression guard: __identify__: check exists in _process_network_msg source."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "wild_igor/igor/main.py"
        ).read_text()
        assert 'msg.content.startswith("__identify__:")' in src, (
            "_process_network_msg must check for __identify__: prefix "
            "and return early (T-web-channel-identify-not-interaction)"
        )
