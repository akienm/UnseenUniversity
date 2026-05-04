"""T-cc1-test-minion: CC.1 haiku-backed skill executor smoke tests.

Unit tests mock the Anthropic client — no real API calls.
The live integration test requires ANTHROPIC_API_KEY and is skipped without it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_tool_use_block(tool_id: str, name: str, input_: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_)


def _make_text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _make_response(content: list, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def _mock_client(responses: list) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = responses
    return client


class TestCC1MinionUnit:
    def test_captures_bash_tool_call(self):
        """run_skill captures a Bash tool_use block in RunResult.tool_calls."""
        from agent_datacenter.test_minions.cc1 import CC1Minion

        bash_block = _make_tool_use_block(
            "tu_1", "Bash", {"command": "cc_queue.py claim T-test"}
        )
        end_response = _make_response(
            [_make_text_block("Done.")], stop_reason="end_turn"
        )
        tool_response = _make_response([bash_block], stop_reason="tool_use")

        client = _mock_client([tool_response, end_response])

        minion = CC1Minion.__new__(CC1Minion)
        minion._model = "claude-haiku-4-5-20251001"
        minion._client = client

        result = minion.run_skill(
            "Claim T-test",
            skill_content="## Step 2\nAlways run: cc_queue.py claim T-test",
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "Bash"
        assert "cc_queue.py claim T-test" in result.tool_calls[0].input["command"]

    def test_no_tool_calls_end_turn(self):
        """When haiku responds with end_turn and no tools, RunResult.tool_calls is empty."""
        from agent_datacenter.test_minions.cc1 import CC1Minion

        client = _mock_client(
            [
                _make_response(
                    [_make_text_block("No tools needed.")], stop_reason="end_turn"
                )
            ]
        )

        minion = CC1Minion.__new__(CC1Minion)
        minion._model = "claude-haiku-4-5-20251001"
        minion._client = client

        result = minion.run_skill("Simple task")

        assert result.tool_calls == []
        assert result.stop_reason == "end_turn"
        assert result.rounds == 1

    def test_respects_max_rounds(self):
        """Loop stops at max_rounds even when haiku keeps returning tool_use."""
        from agent_datacenter.test_minions.cc1 import CC1Minion

        bash_block = _make_tool_use_block("tu_x", "Bash", {"command": "echo loop"})
        tool_response = _make_response([bash_block], stop_reason="tool_use")

        client = _mock_client([tool_response] * 10)

        minion = CC1Minion.__new__(CC1Minion)
        minion._model = "claude-haiku-4-5-20251001"
        minion._client = client

        result = minion.run_skill("Keep calling tools", max_rounds=2)

        assert result.rounds == 2
        assert len(result.tool_calls) == 2

    def test_multiple_tool_calls_accumulated(self):
        """Tool calls from multiple rounds are all captured in tool_calls list."""
        from agent_datacenter.test_minions.cc1 import CC1Minion

        r1 = _make_response(
            [_make_tool_use_block("tu_1", "Bash", {"command": "echo first"})],
            stop_reason="tool_use",
        )
        r2 = _make_response(
            [_make_tool_use_block("tu_2", "Bash", {"command": "echo second"})],
            stop_reason="tool_use",
        )
        r3 = _make_response([_make_text_block("done")], stop_reason="end_turn")

        client = _mock_client([r1, r2, r3])

        minion = CC1Minion.__new__(CC1Minion)
        minion._model = "claude-haiku-4-5-20251001"
        minion._client = client

        result = minion.run_skill("Two steps", max_rounds=5)

        assert len(result.tool_calls) == 2
        commands = [c.input["command"] for c in result.tool_calls]
        assert "echo first" in commands
        assert "echo second" in commands

    def test_skill_path_loaded(self, tmp_path):
        """skill_path argument loads file content as skill."""
        from agent_datacenter.test_minions.cc1 import CC1Minion

        skill_file = tmp_path / "skill.md"
        skill_file.write_text("## Step 1\nAlways run: echo from-file")

        client = _mock_client(
            [_make_response([_make_text_block("done")], stop_reason="end_turn")]
        )

        minion = CC1Minion.__new__(CC1Minion)
        minion._model = "claude-haiku-4-5-20251001"
        minion._client = client

        result = minion.run_skill("Execute step 1", skill_path=skill_file)

        assert result.rounds == 1
        call_args = client.messages.create.call_args
        assert "from-file" in call_args.kwargs.get("system", "")


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live haiku test skipped",
)
class TestCC1MinionLive:
    def test_sprint_claim_step_emits_cc_queue_call(self):
        """Live: haiku following the sprint_claim fixture calls cc_queue.py claim."""
        from agent_datacenter.test_minions.cc1 import CC1Minion
        from agent_datacenter.test_minions.fixtures.sprint_claim import (
            EXPECTED_BASH_PATTERNS,
            SKILL,
            TASK,
        )

        minion = CC1Minion()
        result = minion.run_skill(TASK, skill_content=SKILL)

        assert result.tool_calls, "Expected at least one Bash call, got none"

        bash_commands = [
            c.input.get("command", "") for c in result.tool_calls if c.name == "Bash"
        ]
        for pattern in EXPECTED_BASH_PATTERNS:
            assert any(pattern in cmd for cmd in bash_commands), (
                f"Pattern {pattern!r} not found in any Bash call.\n"
                f"Captured commands: {bash_commands}"
            )
