"""
cursor_runtime.py — T-engram-cursor-runtime

Extracted from main.py: the engram cursor traversal loop. Walks
BRANCHIF chains across engram nodes, spawns FORKIF/SPAWNIF as
background jobs, detects loops via basket snapshot matching.

Also adds CALLIF opcode support: call a registered tool and store
the result in the basket.

Usage (from main.py):
    from .cognition.cursor_runtime import run_cursor
    result = run_cursor(cortex, habit, trigger, basket, job_manager, ...)
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .node_executor import ExecutionResult, execute_node

log = logging.getLogger(__name__)


@dataclass
class CursorResult:
    """Result of a full cursor traversal."""

    nodes_visited: int = 0
    spawned_fork: list[str] = field(default_factory=list)
    spawned_fresh: list[str] = field(default_factory=list)
    stopped_by: str = "end"  # end, loop, error, missing_node
    error: str = ""
    trace: list[str] = field(default_factory=list)  # node_id trail


def run_cursor(
    cortex,
    entry_node,
    trigger: str,
    basket: dict,
    job_manager=None,
    job_completions=None,
    thread_id: str = "",
    max_steps: int = 50,
    log_fn: Optional[Callable] = None,
) -> CursorResult:
    """
    Walk an engram chain starting at entry_node.

    Executes each node via node_executor.execute_node(), follows BRANCHIF
    to the next node, accumulates FORKIF/SPAWNIF targets, and detects
    loops via basket snapshot matching.

    Args:
        cortex: Cortex instance (for node lookup + ring write)
        entry_node: Memory node to start at
        trigger: Which trigger key fired
        basket: Shared basket dict (mutated in place)
        job_manager: Optional JobManager for spawning forks
        job_completions: Queue for fork completion notifications
        thread_id: Thread ID for ring writes
        max_steps: Safety limit on traversal depth
        log_fn: Optional logging function (e.g. rich loginfo)

    Returns CursorResult with traversal summary.
    """
    result = CursorResult()
    visit_log: dict[str, str] = {}  # node_id → basket snapshot
    current_node = entry_node
    current_trigger = trigger

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(f"[dim][ENGRAM] {msg}[/]")
        log.debug("[ENGRAM] %s", msg)

    for step in range(max_steps):
        try:
            exec_result = execute_node(current_node, current_trigger, basket)
        except Exception as exc:
            result.error = str(exc)
            result.stopped_by = "error"
            _log(f"{current_node.id} execution error: {exc}")
            try:
                cortex.write_ring(
                    f"ENGRAM_ERROR|node={current_node.id}|error={str(exc)[:200]}",
                    category="habit_trace",
                    thread_id=thread_id or None,
                )
            except Exception:
                pass
            break

        result.nodes_visited += 1
        result.trace.append(current_node.id)

        _log(
            f"{current_node.id} trigger={current_trigger!r} "
            f"instructions={exec_result.instructions_run} "
            f"stopped_by={exec_result.stopped_by} "
            f"next_node={exec_result.next_node} "
            f"spawned={exec_result.spawned}"
        )

        # Accumulate spawned targets
        result.spawned_fork.extend(exec_result.spawned)
        result.spawned_fresh.extend(exec_result.spawned_fresh)

        # Log branch
        if exec_result.next_node:
            try:
                cortex.write_ring(
                    f"ENGRAM_BRANCH|from={current_node.id}|to={exec_result.next_node}",
                    category="habit_trace",
                    thread_id=thread_id or None,
                )
            except Exception:
                pass

        # No next node — traversal ends
        if not exec_result.next_node:
            result.stopped_by = "end"
            break

        # Fetch next node
        next_memory = cortex.get(exec_result.next_node)
        if not next_memory:
            _log(f"next_node {exec_result.next_node} not found, stopping")
            result.stopped_by = "missing_node"
            break

        # Loop detection via basket snapshot
        snapshot = {k: v for k, v in basket.items() if not k.startswith("_")}
        snapshot_json = json.dumps(snapshot, sort_keys=True, default=str)
        if exec_result.next_node in visit_log:
            if visit_log[exec_result.next_node] == snapshot_json:
                _log(f"loop detected at {exec_result.next_node}, stopping")
                result.stopped_by = "loop"
                break

        visit_log[exec_result.next_node] = snapshot_json
        current_node = next_memory
        current_trigger = exec_result.next_trigger or "__entry__"
    else:
        result.stopped_by = "max_steps"
        _log(f"hit max_steps ({max_steps}), stopping")

    # Dispatch fork targets as background jobs
    if job_manager:
        for spawned_id in result.spawned_fork:
            try:
                cortex.write_ring(
                    f"ENGRAM_FORK|from={entry_node.id}|to={spawned_id}",
                    category="habit_trace",
                    thread_id=thread_id or None,
                )
            except Exception:
                pass
            spawned_node = cortex.get(spawned_id)
            if spawned_node:

                def _exec_fork(_node=spawned_node, _basket=basket):
                    return execute_node(_node, "__entry__", _basket)

                job_manager.submit_background(
                    fn=_exec_fork,
                    title=f"engram_fork:{spawned_id[:16]}",
                    completions_queue=job_completions,
                    thread_id=thread_id or "",
                )

        for spawned_id in result.spawned_fresh:
            try:
                cortex.write_ring(
                    f"ENGRAM_SPAWN|from={entry_node.id}|to={spawned_id}",
                    category="habit_trace",
                    thread_id=thread_id or None,
                )
            except Exception:
                pass
            spawned_node = cortex.get(spawned_id)
            if spawned_node:

                def _exec_spawn(_node=spawned_node):
                    return execute_node(_node, "__entry__", {})

                job_manager.submit_background(
                    fn=_exec_spawn,
                    title=f"engram_spawn:{spawned_id[:16]}",
                    completions_queue=job_completions,
                    thread_id=thread_id or "",
                )

    return result
