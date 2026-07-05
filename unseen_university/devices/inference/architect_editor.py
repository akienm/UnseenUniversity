"""
architect_editor.py — the two-role coding flow (D-coding-loop-redesign-aider-survey-2026-07-04).

A single small model asked to orient + plan + serialize edits in one ReAct stream never
reaches an edit (2026-07-04 DS.0 observe-runs: 0 Write/Edit attempts across 149 tool calls;
it read-wanders and dies in orientation). aider's architect mode splits the jobs: an
ARCHITECT resolves the task into plain file-change instructions; an EDITOR turns those into
an actual edit. This flow runs ONE attempt as that pair and returns a LoopResult the domain's
escalation walk classifies exactly as it classifies a single-loop attempt — so the walk (the
money-safety) is untouched; only 'what one attempt is' changes.

Roles:
  - ARCHITECT: an AgenticLoop offered Read/Bash but NOT Edit/Write (the constraint is
    STRUCTURAL — the tool is not offered — not a prompt request), with a planner system
    prompt. It emits a PLAN (a done-envelope 'plan'/'result' field, or its final text). It
    cannot edit, so it can only plan.
  - EDITOR (e.g. devstral): an AgenticLoop with the full tool set and an 'apply this plan'
    system prompt; the plan rides in its first message. Its narrow job is to serialize the
    plan into Edit/Write calls. Its LoopResult is the attempt's result.

If the architect does not reach DONE (availability/cost/max-turns/escalate), its LoopResult
is returned unchanged — the walk then re-selects or bumps as it would for any attempt, and no
editor run is wasted on a plan that was never produced.

Tier note (D-coding-loop-redesign): the split's value grows when the architect is a STRONGER
model than the editor. Both roles thread `escalation_hop`, so a capability bump lifts both;
the concrete stronger-planner target on Hex is qwen3-coder:30b (bigger than devstral-24b) —
wiring per-role tier selection is follow-up routing work, not this attempt's mechanics.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from unseen_university.devices.inference.agentic_loop import (
    HISTORY_WINDOW_TURNS,
    LOOP_DONE,
    AgenticLoop,
    LoopResult,
    NativeToolCodec,
)

log = logging.getLogger(__name__)

#: The architect may inspect the repo but MUST NOT edit — so it is offered read-only tools.
ARCHITECT_TOOLS = ["Read", "Bash"]

ARCHITECT_PROMPT = """\
You are the ARCHITECT. Resolve the coding task into a concrete PLAN of file changes — do
NOT make the changes yourself (you have NO edit tools; a separate editor applies your plan).
Use Read to read the whole files you need (one Read returns the entire file), and Bash only for
grep/ls. Do NOT run the test suite — reading the code is enough to plan; the editor runs tests.

When you have read enough, STOP reading and write the PLAN: a numbered list of edits, each
naming the absolute file path and the exact change (what to find, what to replace it with, or the
full content for a new file). Keep it specific enough that an editor can apply it without
re-deciding anything.

Emit the plan as a done envelope whose `result` field IS the plan, and nothing else:
{"status": "done", "result": "<numbered file-change plan>", "error_class": null, "error_number": null}
If you write the plan as plain text instead, that is still accepted — but do not keep reading or
say you will implement it yourself. Your whole job is to hand over the numbered plan."""

EDITOR_PROMPT = """\
You are the EDITOR. An EDIT PLAN produced by the architect is given in the first message.
Your only job is to APPLY it: for each planned change, call Edit (exact-string replacement)
or Write (whole file), using absolute paths. Do not re-plan or re-explore beyond what you
need to apply a change. After applying the plan, run the tests named in the plan (or the
ticket) and then signal done."""


# A finish counts as a plan the editor can act on if it names a file path (…/x.py) or is a
# numbered/bulleted list of steps. This is the min-substance guard that keeps the salvage from
# handing empty prose or a bare "I can't do this" to the editor — those still escalate.
_PLAN_FILE_RE = re.compile(r"[\w./-]+\.\w+")          # a path-ish token with an extension
_PLAN_STEP_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S")  # "1. ", "2) ", "- ", "* "


def _is_substantive_plan(text: str) -> bool:
    """True if `text` looks like a real edit plan (names a file OR has numbered/bulleted steps).

    Deliberately permissive on shape (a weak model's plan is rarely clean JSON) but requires SOME
    structure, so empty/garbage/refusal text is not handed to the editor as if it were a plan.
    """
    if not text or len(text.strip()) < 40:
        return False
    return bool(_PLAN_FILE_RE.search(text) or _PLAN_STEP_RE.search(text))


class ArchitectEditorFlow:
    """One coding attempt as an architect(plan)→editor(apply) pair; returns a LoopResult.

    Drop-in for a single AgenticLoop attempt: same inputs, same LoopResult contract, so the
    domain's escalation walk classifies it identically. The split is the whole behavior change.
    """

    def __init__(
        self,
        *,
        critic_enabled: bool = False,
        inference_device=None,
        history_window_turns: int = HISTORY_WINDOW_TURNS,
        aci_mode: bool = False,
    ) -> None:
        self._critic_enabled = critic_enabled
        self._inference_device = inference_device
        self._history_window_turns = history_window_turns
        # Minion-tier ACI (windowed Read + edit-centric tools) applies to BOTH roles — the
        # architect reads to plan and the editor reads to apply, both on the weak local tier.
        self._aci_mode = aci_mode

    def run(
        self,
        *,
        system_prompt: str,
        initial_message: str,
        task_class: str = "worker",
        domain: str = "",
        ticket_id: str = "?",
        agent_id: str = "",
        escalation_hop: int = 0,
        prior_attempt: str = "",
        foreground: bool = False,
        cwd: Path | None = None,
    ) -> LoopResult:
        """Run the architect, then (on a produced plan) the editor. Return the attempt's LoopResult."""
        # 1. ARCHITECT — plan only (no edit tools). Critic is an editor-side concern → off here.
        architect = AgenticLoop(
            codec=NativeToolCodec(),
            critic_enabled=False,
            inference_device=self._inference_device,
            history_window_turns=self._history_window_turns,
            tool_names=ARCHITECT_TOOLS,
            aci_mode=self._aci_mode,
            # Read-only planner: whole-file Read + broad-pytest deflection, so it reads whole
            # files and reaches a plan instead of paging forever (T-architect-read-window-unblock).
            plan_mode=True,
        )
        plan_result = architect.run(
            system_prompt=ARCHITECT_PROMPT + "\n\n" + system_prompt,
            initial_message=initial_message,
            task_class=task_class,
            domain=domain,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
            foreground=foreground,
            cwd=cwd,
        )
        plan = self._extract_plan(plan_result)
        if plan_result.outcome != LOOP_DONE:
            # The architect did not emit a clean done-envelope. But a weak local model routinely
            # produces a REAL plan and then drifts into prose ("Now I'll implement…") or fails to
            # escape its JSON — so json.loads fails, the loop classifies it escalate/max-turns, and
            # the plan is thrown away (observed in the corpus, 2026-07-05). Don't depend on a 24B
            # model emitting escaped JSON: if the finish text is a SUBSTANTIVE plan (names a file
            # path or has numbered steps), accept it and run the editor. Otherwise hand back to the
            # walk unchanged — a garbage/empty finish still escalates, so the walk's re-select and
            # money-safety are untouched.
            if _is_substantive_plan(plan):
                log.info("architect_editor: salvaged a substantive plan from a non-DONE finish "
                         "(%s) for %s — proceeding to editor", plan_result.outcome, ticket_id)
            else:
                log.info("architect_editor: architect did not reach DONE (%s) and produced no "
                         "substantive plan for %s — returning to walk", plan_result.outcome, ticket_id)
                return plan_result
        # Interface crossing (architect → editor handoff): log it.
        log.info("architect_editor: crossing|step=handoff|ticket=%s|plan_chars=%d — handing plan to editor",
                 ticket_id, len(plan))

        # 2. EDITOR — apply the plan with the full tool set; its result IS the attempt's result.
        editor = AgenticLoop(
            codec=NativeToolCodec(),
            critic_enabled=self._critic_enabled,
            inference_device=self._inference_device,
            history_window_turns=self._history_window_turns,
            aci_mode=self._aci_mode,
        )
        editor_message = (
            "## EDIT PLAN (produced by the architect — apply it exactly)\n"
            f"{plan}\n\n"
            "## TICKET (context)\n"
            f"{initial_message}"
        )
        return editor.run(
            system_prompt=EDITOR_PROMPT + "\n\n" + system_prompt,
            initial_message=editor_message,
            task_class=task_class,
            domain=domain,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
            foreground=foreground,
            cwd=cwd,
        )

    @staticmethod
    def _extract_plan(result: LoopResult) -> str:
        """Pull the plan text from the architect's DONE result — envelope 'plan'/'result', else text."""
        env = result.envelope or {}
        return (env.get("plan") or env.get("result") or result.text or "").strip()
