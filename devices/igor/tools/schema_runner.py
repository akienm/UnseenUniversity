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

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_MAX_STEPS = 100  # prevent infinite loops in malformed schemas

_VALID_OPS = frozenset({"==", "!=", "<", ">", "<=", ">=", "in", "not_in"})
_BUILTIN_DOS = frozenset({"prim_set", "prim_branch", "prim_goto"})
_CATALOG_PATH = Path(__file__).parent / "primitives.json"

# ── catalog ─────────────────────────────────────────────────────────────────────


def load_primitives_catalog() -> dict[str, dict]:
    """Load primitives.json and return a dict keyed by primitive id.

    Returns only the implemented (non-missing) entries.
    """
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as _e:
        _log.warning("schema_runner: could not load primitives.json: %s", _e)
        return {}
    return {p["id"]: p for p in data.get("primitives", []) if "id" in p}


def load_missing_primitives() -> list[dict]:
    """Return the list of planned-but-not-yet-implemented primitives."""
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as _e:
        _log.warning("schema_runner: could not load primitives.json: %s", _e)
        return []
    return data.get("missing", [])


# ── validation ───────────────────────────────────────────────────────────────────


def validate_step(step: dict, catalog: dict | None = None) -> list[str]:
    """Validate a single step dict against the primitive catalog.

    Args:
        step:    A step dict from habit.metadata["steps"].
        catalog: Optional preloaded catalog (from load_primitives_catalog()).
                 If None, loads from primitives.json on each call.

    Returns:
        List of error strings. Empty list means valid.
    """
    if catalog is None:
        catalog = load_primitives_catalog()
    errors: list[str] = []

    step_num = step.get("step")
    if not isinstance(step_num, int):
        errors.append("'step' must be an integer")

    do = step.get("do")
    if not do:
        errors.append("'do' is required")
        return errors

    # ── built-in control-flow ops ──────────────────────────────────────────────
    if do == "prim_set":
        if not step.get("key"):
            errors.append("prim_set: 'key' is required")
        if "value" not in step:
            errors.append("prim_set: 'value' is required")

    elif do == "prim_goto":
        if not isinstance(step.get("goto"), int):
            errors.append("prim_goto: 'goto' must be an integer step number")

    elif do == "prim_branch":
        if_spec = step.get("if")
        if not isinstance(if_spec, dict):
            errors.append("prim_branch: 'if' must be a dict {key, op, value}")
        else:
            if not if_spec.get("key"):
                errors.append("prim_branch.if: 'key' is required")
            op = if_spec.get("op")
            if not op:
                errors.append("prim_branch.if: 'op' is required")
            elif op not in _VALID_OPS:
                errors.append(
                    f"prim_branch.if.op {op!r} is not valid; "
                    f"must be one of {sorted(_VALID_OPS)}"
                )
            if "value" not in if_spec:
                errors.append("prim_branch.if: 'value' is required")
        if "goto_true" not in step:
            errors.append("prim_branch: 'goto_true' is required")
        if "goto_false" not in step:
            errors.append("prim_branch: 'goto_false' is required")

    # ── catalog lookup ─────────────────────────────────────────────────────────
    elif do in catalog:
        pass  # known primitive — no additional arg constraints for now

    else:
        errors.append(
            f"unknown primitive {do!r} — not in built-ins or catalog; "
            f"known: {sorted(_BUILTIN_DOS | set(catalog))}"
        )

    return errors


def validate_schema_habit(habit) -> list[str]:
    """Validate all steps of a schema habit. Returns list of error strings."""
    steps_raw = habit.metadata.get("steps")
    if not steps_raw or not isinstance(steps_raw, list):
        return [f"habit {habit.id}: 'steps' missing or not a list"]

    catalog = load_primitives_catalog()
    errors: list[str] = []
    seen_nums: set[int] = set()

    for i, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            errors.append(f"steps[{i}]: expected dict, got {type(step).__name__}")
            continue
        step_num = step.get("step")
        if isinstance(step_num, int) and step_num in seen_nums:
            errors.append(f"steps[{i}]: duplicate step number {step_num}")
        if isinstance(step_num, int):
            seen_nums.add(step_num)
        step_errors = validate_step(step, catalog)
        for e in step_errors:
            errors.append(f"step {step_num}: {e}")

    return errors


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


def run_schema_habit(
    habit, cortex, user_input: str = "", ctx_override: dict | None = None
) -> str:
    """Execute a schema-defined habit from its step list.

    Args:
        habit:        Memory object with metadata["steps"] and optional metadata["ctx_init"].
        cortex:       Cortex instance (used for traversal_start / traversal_set/get).
        user_input:   Original user message — stored in ctx[user_input] for step access.
        ctx_override: Optional dict of ctx values applied after ctx_init (highest priority).
                      Allows callers (e.g. run_habit) to pass runtime args into the habit.

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
    if ctx_override:
        for k, v in ctx_override.items():
            cortex.traversal_set(ctx_id, str(k), str(v), step=0)

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


# ── public composition API ────────────────────────────────────────────────────


def _habit_result(ok: bool, result: str, habit_id: str, steps_run: int, error):
    """Build the standard run_habit result dict."""
    return {
        "ok": ok,
        "result": result,
        "habit_id": habit_id,
        "steps_run": steps_run,
        "error": error,
    }


def run_habit(
    habit_id: str,
    args: dict | None = None,
    *,
    cortex=None,
    user_input: str = "",
    _call_stack: frozenset | None = None,
    max_depth: int = 5,
) -> dict:
    """Execute a habit by ID. Returns {ok, result, habit_id, steps_run, error}.

    Supports schema (step-list) and code_ref (tool dispatch) habits.
    _call_stack / max_depth guard against infinite composition recursion.
    """
    import os as _os
    from pathlib import Path as _Path

    _cs = _call_stack if _call_stack is not None else frozenset()
    _err = lambda msg: _habit_result(False, "", habit_id, 0, msg)
    _ok = lambda r, n=0: _habit_result(True, str(r), habit_id, n, None)

    if habit_id in _cs:
        return _err(f"recursion: {habit_id!r} in call stack {sorted(_cs)}")
    if len(_cs) >= max_depth:
        return _err(f"max depth {max_depth} reached (stack: {sorted(_cs)})")

    if cortex is None:
        db = _os.getenv("IGOR_DB_PATH", "")
        if not db:
            from ..paths import paths as _paths

            db = str(_paths().instance / "wild-0001.db")
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(_Path(db))

    habit = cortex.get(habit_id)
    if habit is None:
        return _err(f"habit {habit_id!r} not found in cortex")

    _cs = _cs | {habit_id}
    habit_type = habit.metadata.get("habit_type", "action")
    ctx_ov = {k: str(v) for k, v in args.items()} if args else None

    try:
        if habit_type == "schema":
            errs = validate_schema_habit(habit)
            if errs:
                return _err("validation: " + "; ".join(errs))
            r = run_schema_habit(habit, cortex, user_input, ctx_override=ctx_ov)
            return _habit_result(
                not r.startswith("[SCHEMA]"), r, habit_id, len(r.splitlines()), None
            )

        code_ref = habit.metadata.get("code_ref")
        if code_ref:
            from .registry import registry as _reg

            tool = _reg.get(code_ref.split(":")[-1])
            if not tool:
                return _err(f"tool {code_ref.split(':')[-1]!r} not in registry")
            r = tool.execute(**(args or {})) if args else tool.execute()
            return _ok(r, n=1)

        actions = habit.metadata.get("actions")
        action = (
            actions[0]
            if isinstance(actions, list) and actions
            else habit.metadata.get("action", "")
        )
        return _ok(action)

    except Exception as _ex:
        return _err(f"unexpected: {_ex}")
