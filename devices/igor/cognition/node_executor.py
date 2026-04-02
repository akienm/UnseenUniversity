"""
node_executor.py — Engram node executor (D260, D290, D291, D295, D296).

Executes one payload cell from a Memory node given a basket.
Called when the cognition cursor lands on a node that has a payload.

Instruction set:
  LABEL    [@name]                           — no-op marker; local jump target for BRANCHIF
  STOPIF   [condition]                       — conditional terminator; if true, stop execution
  EMITIF   [condition, key, value, channel]  — emit value to channel if condition; cursor continues
  BRANCHIF [condition, target_node_id]       — jump to target if condition; cell stops
  FORKIF   [condition, target_node_id]       — spawn new cursor at target if condition; cursor continues
  ENDIF                                       — explicit end; cursor stops (implicit if absent)

Condition format:
  True / False                — constant
  [basket_key, op, rhs]      — eval_gate(basket_key, op, rhs, basket)

Value format in EMITIF:
  constant (int/float/str)   — used directly
  ["basket", key]            — resolved from basket at execution time
  ["payload", field]         — resolved from payload data fields (non-cell fields only)

BRANCHIF targets (D296):
  "@label_name"              — jump to local LABEL node in same cell
  "node_id"                  — set next_node and break; next_trigger = None → "__entry__"
  "node_id#trigger_name"     — set next_node and next_trigger; branch to custom trigger

Data guard:
  The executor only executes payload fields named in memory.triggers.values().
  All other payload fields (NARRATIVE, links, emotional_value, etc.) are data —
  the executor never reads them except for ["payload", field] value lookups in EMITIF.

Returns ExecutionResult:
  next_node   : Optional[str]  — set if BRANCHIF fired with bare node ID or node_id#trigger
  next_trigger: Optional[str]  — trigger name for next_node (D296); None → use "__entry__"
  spawned     : list[str]      — node IDs queued by FORKIF
  basket      : dict           — same basket dict, mutated in place by EMITIF→basket channel
  stopped_by  : str            — implicit_end | ENDIF | BRANCHIF | STOPIF | limit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .eval_gate import eval_gate
from .emit_channels import get_registry

log = logging.getLogger(__name__)

_MAX_INSTRUCTIONS = 200  # guard against runaway cells


@dataclass
class ExecutionResult:
    next_node: Optional[str] = None  # set if BRANCHIF fired with bare node ID
    next_trigger: Optional[str] = (
        None  # trigger name for next_node (D296), or None for "__entry__"
    )
    spawned: list[str] = field(default_factory=list)  # FORKIF targets
    basket: dict = field(default_factory=dict)
    instructions_run: int = 0
    stopped_by: str = "implicit_end"  # implicit_end | ENDIF | BRANCHIF | STOPIF | limit


# ── Condition evaluation ──────────────────────────────────────────────────────


def _eval_condition(condition: Any, basket: dict) -> bool:
    """Evaluate a condition against the basket.

    True/False   → constant
    [key, op, v] → eval_gate(key, op, v, basket)
    """
    if condition is True:
        return True
    if condition is False:
        return False
    if isinstance(condition, list) and len(condition) == 3:
        key, op, rhs = condition
        return eval_gate(str(key), str(op), rhs, basket)
    log.warning("[node_executor] unrecognised condition format: %r", condition)
    return False


# ── Value resolution ──────────────────────────────────────────────────────────


def _resolve_value(value: Any, basket: dict, payload: dict) -> Any:
    """Resolve an EMITIF value to its concrete form.

    ["basket", key]   → basket.get(key)
    ["payload", field] → payload.get(field)  (data fields only — never a cell)
    anything else     → returned as-is
    """
    if isinstance(value, list) and len(value) == 2:
        source, key = value
        if source == "basket":
            return basket.get(key)
        if source == "payload":
            return payload.get(key)
    return value


# ── Executor ──────────────────────────────────────────────────────────────────


def execute_node(memory, fired_trigger: str, basket: dict) -> ExecutionResult:
    """Execute the cell in memory.payload named by fired_trigger.

    Args:
        memory:         Memory object with payload dict and triggers dict.
        fired_trigger:  The trigger string that matched (key in memory.triggers).
        basket:         Shared trail state dict — mutated in place by EMITIF→basket.

    Returns:
        ExecutionResult with next_node, spawned, basket, instructions_run, stopped_by.
        Returns an empty ExecutionResult (no-op) if memory has no payload or no
        matching trigger cell.
    """
    result = ExecutionResult(basket=basket)

    payload = getattr(memory, "payload", None)
    if not payload:
        return result

    triggers = memory.metadata.get("triggers", {})
    cell_name = triggers.get(fired_trigger)
    if not cell_name:
        log.debug(
            "[node_executor] %s: no cell for trigger %r (triggers: %s)",
            memory.id,
            fired_trigger,
            list(triggers.keys()),
        )
        return result

    # Data guard: cell must be a list in the payload — never execute data fields
    cell = payload.get(cell_name)
    if not isinstance(cell, list):
        log.warning(
            "[node_executor] %s: cell %r is not a list (got %s) — skipping",
            memory.id,
            cell_name,
            type(cell).__name__,
        )
        return result

    registry = get_registry()
    n = 0
    i = 0  # index-based loop for BRANCHIF @label jumps

    while i < len(cell):
        if n >= _MAX_INSTRUCTIONS:
            log.warning(
                "[node_executor] %s: hit MAX_INSTRUCTIONS (%d) in cell %r",
                memory.id,
                _MAX_INSTRUCTIONS,
                cell_name,
            )
            result.stopped_by = "limit"
            break

        n += 1
        instruction = cell[i]

        # ENDIF — explicit terminator (check as string literal first)
        if instruction == "ENDIF":
            result.stopped_by = "ENDIF"
            break

        if not isinstance(instruction, list) or not instruction:
            log.warning(
                "[node_executor] %s: malformed instruction %r", memory.id, instruction
            )
            i += 1
            continue

        op = instruction[0]

        # Early check for LABEL and STOPIF which are handled specially
        if op not in ("LABEL", "STOPIF", "EMITIF", "BRANCHIF", "FORKIF"):
            log.warning(
                "[node_executor] unknown instruction op %r in %s", op, memory.id
            )
            i += 1
            continue

        # ── LABEL [@name] ──────────────────────────────────────────────────────
        if op == "LABEL":
            # No-op marker; used as jump target by BRANCHIF @label
            if len(instruction) < 2:
                log.warning(
                    "[node_executor] LABEL expects 1 arg, got %d",
                    len(instruction) - 1,
                )
            log.debug(
                "[node_executor] LABEL: %s → %s",
                memory.id,
                instruction[1] if len(instruction) > 1 else "unnamed",
            )
            i += 1

        # ── STOPIF [condition] ─────────────────────────────────────────────────
        elif op == "STOPIF":
            if len(instruction) != 2:
                log.warning(
                    "[node_executor] STOPIF expects 1 arg, got %d",
                    len(instruction) - 1,
                )
                i += 1
                continue
            _, condition = instruction
            if _eval_condition(condition, basket):
                result.stopped_by = "STOPIF"
                log.debug(
                    "[node_executor] STOPIF fired: %s (condition true)",
                    memory.id,
                )
                break
            i += 1

        # ── EMITIF [condition, key, value, channel] ───────────────────────────
        elif op == "EMITIF":
            if len(instruction) != 5:
                log.warning(
                    "[node_executor] EMITIF expects 4 args, got %d",
                    len(instruction) - 1,
                )
                i += 1
                continue
            _, condition, key, value, channel = instruction
            if _eval_condition(condition, basket):
                resolved = _resolve_value(value, basket, payload)
                registry.write(channel, str(key), resolved, basket)
                log.debug(
                    "[node_executor] EMITIF fired: %s → %s.%s = %r",
                    memory.id,
                    channel,
                    key,
                    resolved,
                )
            i += 1

        # ── BRANCHIF [condition, target_node_id] or [condition, "node_id#trigger_name"] ────────
        elif op == "BRANCHIF":
            if len(instruction) != 3:
                log.warning(
                    "[node_executor] BRANCHIF expects 2 args, got %d",
                    len(instruction) - 1,
                )
                i += 1
                continue
            _, condition, target = instruction
            if _eval_condition(condition, basket):
                target_str = str(target)
                # Check if target is a local @label
                if target_str.startswith("@"):
                    # Scan cell for ["LABEL", target_str] and jump to that index
                    label_found = False
                    for label_idx, instr in enumerate(cell):
                        if (
                            isinstance(instr, list)
                            and len(instr) >= 2
                            and instr[0] == "LABEL"
                            and instr[1] == target_str
                        ):
                            i = label_idx
                            log.debug(
                                "[node_executor] BRANCHIF jumped to label: %s → %s (index %d)",
                                memory.id,
                                target_str,
                                label_idx,
                            )
                            label_found = True
                            break
                    if not label_found:
                        log.warning(
                            "[node_executor] BRANCHIF @label not found: %s → %s",
                            memory.id,
                            target_str,
                        )
                        result.stopped_by = "BRANCHIF"
                        break
                else:
                    # Check for node_id#trigger_name syntax (D296)
                    if "#" in target_str:
                        node_id, trigger_name = target_str.split("#", 1)
                        result.next_node = node_id
                        result.next_trigger = trigger_name
                        log.debug(
                            "[node_executor] BRANCHIF fired with trigger: %s → %s#%s",
                            memory.id,
                            node_id,
                            trigger_name,
                        )
                    else:
                        # Bare node ID: set next_node, next_trigger = None
                        result.next_node = target_str
                        result.next_trigger = None
                        log.debug(
                            "[node_executor] BRANCHIF fired: %s → %s",
                            memory.id,
                            target_str,
                        )
                    result.stopped_by = "BRANCHIF"
                    break
            else:
                i += 1

        # ── FORKIF [condition, target_node_id] ───────────────────────────────
        elif op == "FORKIF":
            if len(instruction) != 3:
                log.warning(
                    "[node_executor] FORKIF expects 2 args, got %d",
                    len(instruction) - 1,
                )
                i += 1
                continue
            _, condition, target = instruction
            if _eval_condition(condition, basket):
                target_str = str(target) if target is not None else ""
                if target_str and target_str != "None":
                    result.spawned.append(target_str)
                    log.debug(
                        "[node_executor] FORKIF spawned: %s → %s",
                        memory.id,
                        target_str,
                    )
            # cursor continues regardless
            i += 1

    result.instructions_run = n
    return result
