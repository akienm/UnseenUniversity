"""CC1Minion — Haiku-backed skill executor for integration testing.

Wraps the Anthropic client with a simple run_skill() loop that:
  1. Sends the task + skill content as a system prompt
  2. Captures all tool_use blocks across multiple rounds
  3. Stops at end_turn or max_rounds

Used by tests to verify that skill files produce the expected tool calls
without running a full CC session.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    name: str
    input: dict
    id: str = ""


@dataclass
class RunResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    rounds: int = 0
    final_text: str = ""
    stop_reason: str = "end_turn"


_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PREAMBLE = (
    "You are a test minion executing a skill. "
    "Follow the skill instructions exactly. "
    "Use the Bash tool to run commands as instructed."
)


class CC1Minion:
    """Haiku-backed skill executor.

    Instantiate with no args for live use (reads ANTHROPIC_API_KEY from env).
    For unit tests, set _client and _model directly after CC1Minion.__new__(CC1Minion).
    """

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        import anthropic
        self._model = model
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def run_skill(
        self,
        task: str,
        skill_content: str | None = None,
        skill_path: Path | str | None = None,
        max_rounds: int = 10,
    ) -> RunResult:
        """Run the task following the skill instructions.

        Args:
            task: The user-facing task description.
            skill_content: Skill markdown content as a string.
            skill_path: Path to a skill .md file (loaded if skill_content is None).
            max_rounds: Maximum tool-use rounds before stopping.

        Returns:
            RunResult with all captured tool calls and round count.
        """
        if skill_content is None and skill_path is not None:
            skill_content = Path(skill_path).read_text(encoding="utf-8")

        system = _SYSTEM_PREAMBLE
        if skill_content:
            system = f"{_SYSTEM_PREAMBLE}\n\nSkill instructions:\n{skill_content}"

        tools = [
            {
                "name": "Bash",
                "description": "Run a bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The bash command to run"}
                    },
                    "required": ["command"],
                },
            }
        ]

        messages = [{"role": "user", "content": task}]
        result = RunResult()

        for _ in range(max_rounds):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                tools=tools,
                messages=messages,
            )
            result.rounds += 1

            tool_calls_this_round = []
            text_this_round = []

            for block in response.content:
                if block.type == "tool_use":
                    tc = ToolCall(name=block.name, input=block.input, id=block.id)
                    result.tool_calls.append(tc)
                    tool_calls_this_round.append(block)
                elif block.type == "text":
                    text_this_round.append(block.text)

            if text_this_round:
                result.final_text = text_this_round[-1]

            result.stop_reason = response.stop_reason
            if response.stop_reason == "end_turn" or not tool_calls_this_round:
                break

            # Append assistant turn + synthetic tool results for next round
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "(executed)",
                }
                for block in tool_calls_this_round
            ]
            messages.append({"role": "user", "content": tool_results})

        return result
