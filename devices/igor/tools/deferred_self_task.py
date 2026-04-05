"""
deferred_self_task.py — T-igor-deferred-self-tasks

Igor is a single-pass system. Without deferred self-addressed tasks he can
diagnose a gap but can't fetch the data he needs and re-enter the same turn.

This module provides:
  parse_deferred_tasks(text)   — extract DEFERRED_TASK blocks from reply text
  dispatch_deferred_task(...)  — submit parsed task as a background job
  strip_deferred_tasks(text)   — return text with DEFERRED_TASK lines removed

Format Igor emits in his reply:
  DEFERRED_TASK|memory_search|<query>
  DEFERRED_TASK|twm_read|
  DEFERRED_TASK|ring_read|<category>
  DEFERRED_TASK|tool_call|<tool_name>|<args_json>
  DEFERRED_TASK|note|<text>

When the background job completes the result is pushed to TWM with
source="deferred_self_task" and salience=0.85 — visible on the next turn.

Design principle (D—, 2026-04-04): Igor's self-awareness must be grounded in
real context, not confabulation. Deferred tasks let him say "I need X before I
can answer reliably" and ensure X arrives before the next reply.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

_TASK_RE = re.compile(r"^DEFERRED_TASK\|(.+)$", re.MULTILINE)

if TYPE_CHECKING:
    from ..memory.cortex import Cortex


# ── parsing ───────────────────────────────────────────────────────────────────


def parse_deferred_tasks(text: str) -> list[dict[str, Any]]:
    """
    Return a list of parsed task dicts from DEFERRED_TASK| lines in text.

    Each dict has: type, payload (str), raw (original line).
    """
    tasks = []
    for m in _TASK_RE.finditer(text):
        raw_payload = m.group(1)
        parts = raw_payload.split("|", 1)
        task_type = parts[0].strip().lower()
        payload = parts[1].strip() if len(parts) > 1 else ""
        tasks.append({"type": task_type, "payload": payload, "raw": m.group(0)})
    return tasks


def strip_deferred_tasks(text: str) -> str:
    """Remove all DEFERRED_TASK lines from text before showing to the user."""
    return _TASK_RE.sub("", text).strip()


# ── dispatch ──────────────────────────────────────────────────────────────────


def dispatch_deferred_task(
    task: dict[str, Any],
    cortex: "Cortex",
    job_manager: Any,
    completions_queue: Any,
    thread_id: str = "",
) -> str | None:
    """
    Submit a parsed deferred task as a background job.

    Returns the job_id, or None if the task type is unsupported.
    The job result is pushed to TWM by the completion callback
    (_push_result_to_twm — called from IgorAgent._process_completions).
    The TWM push happens in the completions queue handler, not here, so the
    result is visible on the *next* turn (not the current one).
    """
    task_type = task["type"]
    payload = task["payload"]

    fn = _make_job_fn(task_type, payload, cortex)
    if fn is None:
        logger.warning("deferred_self_task: unsupported type %r — skipped", task_type)
        return None

    title = f"deferred_self_task:{task_type}:{payload[:40]}"
    job_id = job_manager.submit_background(
        fn=fn,
        title=title,
        completions_queue=completions_queue,
        thread_id=thread_id,
    )
    logger.info("deferred_self_task: submitted %s job_id=%s", task_type, job_id)
    return job_id


def _make_job_fn(task_type: str, payload: str, cortex: "Cortex"):
    """Return a zero-arg callable for the given task type, or None."""

    if task_type == "memory_search":
        query = payload

        def _fn():
            try:
                results = cortex.search(query, limit=5)
                if not results:
                    return f"memory_search({query!r}): no results"
                lines = [f"memory_search({query!r}): {len(results)} hit(s)"]
                for m in results:
                    lines.append(f"  [{m.memory_type}] {m.narrative[:120]}")
                return "\n".join(lines)
            except Exception as e:
                return f"memory_search({query!r}): error — {e}"

        return _fn

    if task_type == "twm_read":

        def _fn():
            try:
                items = cortex.twm_read(limit=10)
                if not items:
                    return "twm_read: TWM is empty"
                lines = [f"twm_read: {len(items)} item(s)"]
                for item in items:
                    src = item.get("source", "?")
                    u = item.get("urgency", 0)
                    s = item.get("salience", 0)
                    c = item.get("content_csb", "")[:80]
                    lines.append(f"  [{src}] u={u:.2f} s={s:.2f}  {c}")
                return "\n".join(lines)
            except Exception as e:
                return f"twm_read: error — {e}"

        return _fn

    if task_type == "ring_read":
        category = payload or None

        def _fn():
            try:
                items = cortex.ring_read(limit=10, category=category)
                if not items:
                    return f"ring_read(category={category!r}): empty"
                lines = [f"ring_read(category={category!r}): {len(items)} item(s)"]
                for item in items:
                    lines.append(f"  {str(item)[:120]}")
                return "\n".join(lines)
            except Exception as e:
                return f"ring_read(category={category!r}): error — {e}"

        return _fn

    if task_type == "tool_call":
        # payload: "<tool_name>|<args_json>"  or just "<tool_name>"
        parts = payload.split("|", 1)
        tool_name = parts[0].strip()
        args_raw = parts[1].strip() if len(parts) > 1 else "{}"

        def _fn():
            try:
                args = json.loads(args_raw) if args_raw else {}
                from .registry import registry

                tool = registry.get(tool_name)
                if tool is None:
                    return f"tool_call({tool_name!r}): tool not found"
                result = tool.fn(**args)
                return f"tool_call({tool_name!r}): {str(result)[:400]}"
            except Exception as e:
                return f"tool_call({tool_name!r}): error — {e}"

        return _fn

    if task_type == "note":
        # Igor wants to inject a plain note into his own next-turn context.
        note_text = payload

        def _fn():
            return f"self_note: {note_text}"

        return _fn

    return None


def push_deferred_result_to_twm(
    cortex: "Cortex",
    job_id: str,
    title: str,
    result: str,
    thread_id: str = "",
) -> None:
    """
    Called from IgorAgent._process_completions when a deferred_self_task job
    completes. Pushes result to TWM so it is visible on the next turn.
    """
    if not title.startswith("deferred_self_task:"):
        return  # not a deferred self-task job — skip
    try:
        cortex.twm_push(
            content_csb=f"DEFERRED_RESULT|job_id={job_id}|{result[:300]}",
            source="deferred_self_task",
            salience=0.85,
            urgency=0.35,  # visible but not screaming
            ttl_seconds=600,
            thread_id=thread_id or None,
        )
        logger.info("deferred_self_task: pushed result to TWM job_id=%s", job_id)
    except Exception as e:
        logger.warning("deferred_self_task: TWM push failed job_id=%s: %s", job_id, e)
