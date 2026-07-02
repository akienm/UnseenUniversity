"""
tool_loop.py — minion's thin adapter over the shared AgenticLoop.

Minion-tier models (qwen3.5-9b, llama3.2:3b) don't declare features=['tools'], so they
can't do native tool-calling; minion drives the ONE shared loop with the XML TextToolCodec
(D-domain-object-encapsulation: one loop mechanism, pluggable codec). This module is now
just the WorkerEnvelope↔AgenticLoop adapter — the turn-runner, tool execution, and XML
parsing live in inference/agentic_loop.py. Minion keeps its own WorkerEnvelope/WorkerResult
contract and its XML-protocol system prompt (unchanged behavior); only the duplicate loop
mechanism is gone.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from unseen_university.devices.inference.agentic_loop import (
    LOOP_DONE,
    AgenticLoop,
    TextToolCodec,
)
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.minion.shim import WorkerEnvelope, WorkerResult

log = logging.getLogger(__name__)

_MAX_ITERATIONS = int(os.environ.get("MINION_MAX_ITERATIONS", "20"))


_SYSTEM_PROMPT = """You are a MINION-tier code worker in the Unseen University agent rack.

TIER: minion
TASK: Complete one ticket. Make targeted edits, run tests, commit, close.

ESCALATION — respond with a signal line if you hit a wall:
  ESCALATE: worker   — after 3 failed attempts on any subtask
  ESCALATE: analyst  — ticket requires design decisions or cross-file reasoning
  ESCALATE: designer — safety/auth/multi-device coordination, or scope is wrong

Put the escalation signal on its own line, then explain: what you tried, what blocked you.

DONE SIGNAL — when tests pass and the commit is made:
  DONE: <one-line summary of what changed>

TOOL CALLS — output ONE tool call block per response, then stop and wait for the result:

  Read a file:
  <tool>Read</tool><path>path/to/file</path>

  Run a shell command:
  <tool>Bash</tool><command>pytest tests/ -q --tb=short 2>&1 | head -60</command>

  Edit a file (replace exact text — whitespace must match exactly):
  <tool>Edit</tool><path>path/to/file</path>
  <old_string>exact text to replace including whitespace</old_string>
  <new_string>replacement text</new_string>

  Write a new file:
  <tool>Write</tool><path>path/to/file</path>
  <content>full file content here</content>

RULES:
- One tool call per turn. Wait for the result before the next.
- Always run tests before DONE: python3 -m pytest tests/ -q --tb=short 2>&1 | head -60
- After 3 consecutive non-zero bash exits on the same issue: ESCALATE: worker
- Commit with: git add <files> && git commit -m "feat: <summary>"
- Never push — the rack handles that.

CONTEXT (repo map):
{repo_map}

TICKET: {ticket_id}
{description}"""


class ToolLoop:
    """Adapts a WorkerEnvelope onto the shared AgenticLoop (XML text codec, no cost cap).

    Kept as minion's boundary: builds minion's system prompt, runs one shared-loop attempt,
    and maps the typed LoopResult back to a WorkerResult (preserving the DONE / tier-targeted
    ESCALATE signal). Minion has NO difficulty walk — it runs once and returns done/escalate.
    """

    def __init__(
        self,
        inference: InferenceDevice,
        cwd: Path | None = None,
        max_iterations: int = _MAX_ITERATIONS,
    ) -> None:
        self._inference = inference
        self._default_cwd = cwd or Path.cwd()
        self._max_iterations = max_iterations

    def run(self, envelope: WorkerEnvelope) -> WorkerResult:
        """Run the shared loop for one ticket. Returns WorkerResult."""
        cwd = Path(envelope.cwd) if envelope.cwd else self._default_cwd
        system = _SYSTEM_PROMPT.format(
            repo_map=envelope.repo_map or "(not available — read files you need)",
            ticket_id=envelope.ticket_id,
            description=envelope.description,
        )
        loop = AgenticLoop(
            codec=TextToolCodec(),
            max_turns=self._max_iterations,
            cost_cap_usd=None,  # minion has no per-run cost cap (unchanged from prior behavior)
            critic_enabled=False,
            inference_device=self._inference,
        )
        result = loop.run(
            system_prompt=system,
            initial_message=f"Begin working on ticket {envelope.ticket_id}.",
            task_class=envelope.task_class,
            session_id=envelope.session_id,
            ticket_id=envelope.ticket_id,
            agent_id="minion",
            cwd=cwd,
        )
        signal, notes = _map_signal(result)
        log.info("ToolLoop: %s → signal=%r for %s", result.outcome, signal, envelope.ticket_id)
        return WorkerResult(
            signal=signal,
            notes=notes,
            iterations=result.turns,
            tools_called=result.tools_called,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )


def _map_signal(result) -> tuple[str, str]:
    """Map a LoopResult to a minion (signal, notes) pair, preserving the escalate target."""
    env = result.envelope or {}
    if result.outcome == LOOP_DONE:
        return "DONE", env.get("result", result.text)
    # Any non-done terminal is an escalation. A model ESCALATE carries its target tier;
    # max-turns / availability / error default to the worker tier (unchanged behavior).
    target = env.get("target", "worker")
    return f"ESCALATE: {target}", env.get("result", result.text)
