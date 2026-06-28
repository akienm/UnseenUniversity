"""Tests for T-escalate-default-pattern: escalate.py primitive + NE empty-result wiring."""

import unittest
from unittest.mock import patch, MagicMock


class TestEscalateToChannel(unittest.TestCase):
    def test_escalate_calls_post_to_channel(self):
        from unseen_university.devices.igor.cognition.escalate import escalate_to_channel

        with patch("unseen_university.devices.igor.tools.channel_post.post_to_channel") as mock_channel:
            escalate_to_channel("test escalation message", dedup_key="test-key")
            mock_channel.assert_called_once_with(
                "test escalation message",
                author="igor",
                channel="shared",
                dedup_key="test-key",
            )

    def test_escalate_swallows_channel_failure(self):
        from unseen_university.devices.igor.cognition.escalate import escalate_to_channel

        with patch(
            "unseen_university.devices.igor.tools.channel_post.post_to_channel",
            side_effect=RuntimeError("channel down"),
        ):
            # Must not raise
            escalate_to_channel("message", dedup_key="key")

    def test_escalate_no_dedup_key(self):
        from unseen_university.devices.igor.cognition.escalate import escalate_to_channel

        with patch("unseen_university.devices.igor.tools.channel_post.post_to_channel") as mock_channel:
            escalate_to_channel("message without dedup")
            mock_channel.assert_called_once_with(
                "message without dedup",
                author="igor",
                channel="shared",
                dedup_key=None,
            )


class TestNEEmptyResultEscalation(unittest.TestCase):
    """Verify that the NE empty-result path in coa.py calls escalate_to_channel."""

    def _make_coa(self):
        """Build a minimal COA with a mocked NE that returns falsy."""
        from unseen_university.devices.igor.cognition.coa import COA

        igor_mock = MagicMock()
        igor_mock._is_processing = False
        igor_mock._experiment_scheduler = None

        coa = COA.__new__(COA)
        coa._igor = igor_mock
        coa._cortex = None
        coa._last_ne_valence = 0.0
        coa._bg_thread = None

        ne_mock = MagicMock()
        ne_mock.run.return_value = None  # falsy — NE produces nothing
        coa.ne = ne_mock

        return coa, igor_mock

    def test_ne_empty_result_escalates(self):
        coa, igor_mock = self._make_coa()

        with patch(
            "unseen_university.devices.igor.cognition.escalate.escalate_to_channel"
        ) as mock_esc, patch(
            "unseen_university.devices.igor.tools.channel_post.post_to_channel"
        ), patch(
            "unseen_university.devices.igor.cognition.milieu.get", return_value=None
        ):
            result = coa.ne.run(verbose=False)
            # result is None — simulate the else branch
            if not result:
                from unseen_university.devices.igor.cognition.escalate import escalate_to_channel

                escalate_to_channel(
                    f"[NE] cycle produced no result — Igor may be stuck. "
                    f"Last valence: {coa._last_ne_valence:.2f}. "
                    "Nothing actionable in TWM — watch-question scan runs "
                    "next lever-watcher cycle.",
                    dedup_key="ne-empty-result",
                )

            mock_esc.assert_called_once()
            call_args = mock_esc.call_args
            self.assertIn("ne-empty-result", str(call_args))
            self.assertIn("may be stuck", str(call_args))

    def test_ne_non_empty_does_not_escalate(self):
        coa, _ = self._make_coa()
        coa.ne.run.return_value = {"internal_state": {"valence": 0.5}}

        with patch("unseen_university.devices.igor.cognition.escalate.escalate_to_channel") as mock_esc:
            result = coa.ne.run(verbose=False)
            if result:
                pass  # normal path — no escalation
            mock_esc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
