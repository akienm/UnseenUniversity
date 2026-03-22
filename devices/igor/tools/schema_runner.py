"""
schema_runner.py — Step-list executor for schema-defined habits (T-habit-schema).

A schema habit stores its logic as a JSON step list in habit.metadata["steps"].
Each step specifies a primitive (tool name) to call or a built-in control-flow op.
The runner executes steps in order, using a traversal_contexts row as shared state.

Schema format (everything in habit.metadata):

    habit_type: "schema"
    trigger:    "keywords ..."
    ctx_init:   {"dir": "~/path", "read_head_lines": "20"}   # optional init values
    steps: [
        {"step": 1, "do": "prim_list_dir"},
        {"step": 2, "do": "prim_iter_done"},
        {"step": 3, "do": "prim_branch",
                    "if": {"key": "done", "op": "==", "value": "true"},
                    "goto_true": 6, "goto_false": 4},
        {"step": 4, "do": "prim_iter_next"},
        {"step": 5, "do": "prim_goto", "goto": 2},
        {"step": 6, "do": "prim_set", "key": "result", "value": "scan complete"},
    ]

Built-in do values (no tool registry lookup):
    prim_set    — write ctx[key] = value (literal string)
    prim_branch — conditional goto on ctx value comparison
    prim_goto   — unconditional jump to given step number

Condition ops for prim_branch.if:
    == != < > <= >= in not_in

Any other do value is looked up in the tool registry and called with no args
(primitives read their inputs from the traversal context, per os_primitives convention).

Acceptance: no Python import needed to define or extend a schema habit's logic.
Add a new primitive to the tool registry and it's immediately usable in step lists.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

_MAX_STEPS = 100  # prevent infinite loops in malformed schemas


def _eval_condition(ctx_val: str, op: str, cmp_val: str) -> bool:
    """Evaluate ctx_val <op> cmp_val to a boolean."""
    if op == "==":
        return ctx_val == cmp_val
    if op == "!=":
        return ctx_val != cmp_val
    if op == "in":
        return cmp_val in ctx_val
    if op == "not_in":
        return cmp_val not in ctx_val
    # Numeric comparisons
    try:
        a = float(ctx_val)
        b = float(cmp_val)
        if op == "<":
            return a < b
        if op == ">":
            return a > b
        if op == "<=":
            return a <= b
        if op == ">=":
            return a >= b
    except (ValueError, TypeError):
        pass
    return False


def run_schema_habit(habit, cortex, user_input: str = "") -> str:
    """Execute a schema-defined habit from its step list.

    Args:
        habit:      Memory object with metadata["steps"] and optional metadata["ctx_init"].
        cortex:     Cortex instance (used for traversal_start / traversal_set/get).
        user_input: Original user message — stored in ctx[user_input] for step access.

    Returns:
        Newline-joined step result strings.
    """
    steps_raw = habit.metadata.get("steps")
    if not steps_raw or not isinstance(steps_raw, list):
        return f"[SCHEMA] habit {habit.id}: no steps defined"

    # Build step map: step_number → step_dict (validate integers)
    step_map: dict[int, dict[str, Any]] = {}
    for s in steps_raw:
        if not isinstance(s, dict):
            continue
        num = s.get("step")
        if isinstance(num, int):
            step_map[num] = s

    if not step_map:
        return f"[SCHEMA] habit {habit.id}: step list has no valid step entries"

    # ── init traversal context ──────────────────────────────────────────────────
    ctx_id = cortex.traversal_start(job_id=f"schema:{habit.id}")

    ctx_init = habit.metadata.get("ctx_init", {})
    for k, v in ctx_init.items():
        cortex.traversal_set(ctx_id, str(k), str(v), step=0)
    if user_input:
        cortex.traversal_set(ctx_id, "user_input", user_input, step=0)

    # ── step execution loop ─────────────────────────────────────────────────────
    from .registry import registry as _tool_registry

    current_step = min(step_map.keys())
    results: list[str] = []
    iterations = 0

    while iterations < _MAX_STEPS:
        iterations += 1
        step = step_map.get(current_step)
        if step is None:
            break  # stepped past end of step_map → done

        do = step.get("do", "")
        next_step = current_step + 1  # default: advance to next step

        # ── built-in control-flow (no tool registry) ──────────────────────────
        if do == "prim_set":
            key = step.get("key", "")
            val = str(step.get("value", ""))
            cortex.traversal_set(ctx_id, key, val, step=current_step)
            results.append(f"SET {key}={val!r}")

        elif do == "prim_goto":
            goto = step.get("goto")
            if isinstance(goto, int):
                next_step = goto
            results.append(f"GOTO {next_step}")

        elif do == "prim_branch":
            if_spec = step.get("if", {})
            if isinstance(if_spec, dict):
                ctx_val = cortex.traversal_get(ctx_id, if_spec.get("key", "")) or ""
                op = str(if_spec.get("op", "=="))
                cmp_val = str(if_spec.get("value", ""))
                cond = _eval_condition(ctx_val, op, cmp_val)
            else:
                cond = False
            branch_dest = "goto_true" if cond else "goto_false"
            next_step = step.get(branch_dest, current_step + 1)
            results.append(
                f"BRANCH {if_spec.get('key', '?')} {if_spec.get('op', '?')} "
                f"{if_spec.get('value', '?')} → cond={cond} → step {next_step}"
            )

        # ── tool registry dispatch ─────────────────────────────────────────────
        elif do:
            tool = _tool_registry.get(do)
            if tool:
                try:
                    result = tool.execute()
                    results.append(str(result))
                except Exception as _e:
                    results.append(f"[SCHEMA:{do}] error: {_e}")
                    _log.warning(
                        "schema_runner: step %d primitive %r failed: %s",
                        current_step,
                        do,
                        _e,
                    )
                    break
            else:
                results.append(f"[SCHEMA] unknown primitive: {do!r}")
                _log.warning(
                    "schema_runner: habit %s step %d: unknown primitive %r",
                    habit.id,
                    current_step,
                    do,
                )
                break

        # ── advance ───────────────────────────────────────────────────────────
        if next_step not in step_map:
            break
        current_step = next_step

    if iterations >= _MAX_STEPS:
        results.append(
            f"[SCHEMA] reached MAX_STEPS ({_MAX_STEPS}) — possible infinite loop in {habit.id}"
        )

    return "\n".join(results) if results else f"[SCHEMA] {habit.id}: no output"
