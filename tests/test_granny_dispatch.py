"""Tests for devices.granny.dispatch — cc_dispatch_fn and inference_dispatch_fn."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from devices.granny.dispatch import cc_dispatch_fn


def _ticket(id="T-test-dispatch", size="S", tags=None):
    return {
        "id": id,
        "title": "test dispatch ticket",
        "size": size,
        "tags": tags or ["Platform"],
    }


class TestCcDispatchFn:
    def test_returns_true_on_success(self):
        with (
            patch("subprocess.run") as mock_run,
            patch("unseen_university.channel.post_to_channel"),
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
        assert channel == "granny-weatherwax"

    def test_returns_false_on_missing_id(self):
        result = cc_dispatch_fn({"title": "no id"})
        assert result is False

    def test_still_posts_channel_if_queue_dispatch_fails(self):
        posted = []
        with (
            patch("subprocess.run", side_effect=Exception("queue down")),
            patch(
                "unseen_university.channel.post_to_channel",
                side_effect=lambda *a, **kw: posted.append(a),
            ),
        ):
            result = cc_dispatch_fn(_ticket())
        assert result is True
        assert len(posted) == 1

    def test_no_spawn_on_dispatch(self):
        """cc_dispatch_fn no longer spawns processes — send-keys is handled by T-granny-cc0-dispatch."""
        import subprocess as sp

        with (
            patch("subprocess.run") as mock_run,
            patch("unseen_university.channel.post_to_channel"),
            patch.object(sp, "Popen") as mock_popen,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            cc_dispatch_fn(_ticket("T-no-spawn"))
        mock_popen.assert_not_called()


class TestInferenceDispatchFn:
    def _worker_result(self, signal="DONE", notes="ok", task_class="worker"):
        from devices.minion.shim import WorkerResult

        return WorkerResult(
            signal=signal,
            notes=notes,
            iterations=1,
            round_count=1,
            advisor_calls=0,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0001,
        )

    def _ticket(self, id="T-inf-test", tags=None):
        return {
            "id": id,
            "title": "test inference ticket",
            "size": "S",
            "tags": tags or ["Platform"],
            "description": "do the thing",
        }

    def test_returns_true_on_success(self):
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        with (
            patch("subprocess.run") as mock_run,
            patch("unseen_university.channel.post_to_channel"),
            patch.object(MinionDevice, "execute", return_value=self._worker_result()),
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = inference_dispatch_fn(self._ticket())
        assert result is True

    def test_analyst_task_class_for_non_minion_ticket(self):
        """Non-minion tickets start the cascade at analyst tier."""
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        captured_envelopes = []

        def _capture(envelope):
            captured_envelopes.append(envelope)
            return self._worker_result()

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(MinionDevice, "execute", side_effect=_capture),
        ):
            inference_dispatch_fn(self._ticket(tags=["Platform", "Infrastructure"]))

        assert len(captured_envelopes) == 1
        assert captured_envelopes[0].task_class == "analyst"

    def test_minion_task_class_for_minion_tagged_ticket(self):
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        captured_envelopes = []

        def _capture(envelope):
            captured_envelopes.append(envelope)
            return self._worker_result(task_class="minion")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(MinionDevice, "execute", side_effect=_capture),
        ):
            inference_dispatch_fn(self._ticket(tags=["minion", "Platform"]))

        assert len(captured_envelopes) == 1
        assert captured_envelopes[0].task_class == "minion"

    def test_posts_minion_result_to_channel(self):
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        posted = []

        def capture(msg, author, channel):
            posted.append(msg)

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel", side_effect=capture),
            patch.object(MinionDevice, "execute", return_value=self._worker_result()),
        ):
            inference_dispatch_fn(self._ticket())

        result_msgs = [m for m in posted if m.startswith("MINION_RESULT")]
        assert len(result_msgs) == 1
        assert "tier=analyst" in result_msgs[0]

    def test_returns_false_on_missing_id(self):
        from devices.granny.dispatch import inference_dispatch_fn

        result = inference_dispatch_fn({"title": "no id"})
        assert result is False

    def test_returns_false_on_minion_execute_error(self):
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(
                MinionDevice, "execute", side_effect=RuntimeError("minion crashed")
            ),
        ):
            result = inference_dispatch_fn(self._ticket())
        assert result is False

    def test_cascade_tries_next_tier_on_escalate(self):
        """On ESCALATE from tier 1, tier 2 runs with escalation_history populated."""
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        call_count = [0]
        captured = []

        def _execute(envelope):
            call_count[0] += 1
            captured.append((envelope.task_class, list(envelope.escalation_history)))
            if call_count[0] == 1:
                return self._worker_result(signal="ESCALATE: worker")
            return self._worker_result(signal="DONE")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(MinionDevice, "execute", side_effect=_execute),
        ):
            result = inference_dispatch_fn(self._ticket())

        assert result is True
        assert call_count[0] == 2
        assert captured[0][0] == "analyst"
        assert captured[1][0] == "worker"
        assert len(captured[1][1]) == 1
        assert captured[1][1][0]["tier"] == "analyst"

    def test_cascade_blocks_for_cc_when_all_tiers_exhausted(self):
        """When all OR tiers ESCALATE, ticket is blocked (not dispatched to CC)."""
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        block_calls = []

        def _mock_run(cmd, **kwargs):
            if "block" in cmd:
                block_calls.append(cmd)
            return MagicMock(returncode=0, stderr="")

        with (
            patch("subprocess.run", side_effect=_mock_run),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(
                MinionDevice,
                "execute",
                return_value=self._worker_result(signal="ESCALATE: analyst"),
            ),
        ):
            result = inference_dispatch_fn(self._ticket())

        assert result is True
        assert any(
            "block" in " ".join(c) for c in block_calls
        ), "expected cc_queue.py block to be called when all tiers exhausted"

    def test_cascade_posts_or_tier_escalate_on_escalation(self):
        """OR_TIER_ESCALATE is posted to channel when a tier escalates."""
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        posted = []
        call_count = [0]

        def _execute(envelope):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._worker_result(signal="ESCALATE: worker")
            return self._worker_result(signal="DONE")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch(
                "unseen_university.channel.post_to_channel",
                side_effect=lambda m, **kw: posted.append(m),
            ),
            patch.object(MinionDevice, "execute", side_effect=_execute),
        ):
            inference_dispatch_fn(self._ticket())

        escalate_msgs = [m for m in posted if m.startswith("OR_TIER_ESCALATE")]
        assert len(escalate_msgs) == 1
        assert "from=analyst" in escalate_msgs[0]

    def test_minion_tag_skips_directly_to_minion_tier(self):
        """minion-tagged tickets skip analyst+worker and go straight to minion."""
        from devices.granny.dispatch import inference_dispatch_fn
        from devices.minion.device import MinionDevice

        captured = []

        def _execute(envelope):
            captured.append(envelope.task_class)
            return self._worker_result(signal="DONE")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            patch("unseen_university.channel.post_to_channel"),
            patch.object(MinionDevice, "execute", side_effect=_execute),
        ):
            inference_dispatch_fn(self._ticket(tags=["minion"]))

        assert captured == ["minion"]
