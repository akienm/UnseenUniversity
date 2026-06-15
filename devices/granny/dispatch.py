"""
dispatch.py — Dispatch functions for GrannyWeatherwaxDevice routing edges.

Each dispatch_fn takes a ticket dict and returns bool (True=dispatched).
The CC dispatch function:
  1. Calls cc_queue.py dispatch <ticket_id> to set the ticket in_progress
  2. Posts GRANNY_DISPATCH to the shared channel for observability
  3. Spawns a detached tmux session running 'claude -p /sprint-ticket <id>'

The inference dispatch function routes tickets to a cheap model via InferenceDevice:
  1. Calls cc_queue.py dispatch <ticket_id> to set in_progress
  2. Posts GRANNY_DISPATCH|worker=<task_class> to the shared channel
  3. Sends the ticket description to InferenceDevice
     - task_class='minion' for tickets tagged 'minion' (→ qwen via OR)
     - task_class='worker' for all others (→ deepseek-v4-flash via OR)
  4. Logs token cost at INFO level + posts INFERENCE_COST to channel
  5. Submits result via cc_queue.py done (awaiting_validation, not auto-close)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Repo root — where CC must run so it picks up CLAUDE.md and project context.
_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
# Always use UU's own cc_queue.py — never inherited CC_WORKFLOW_TOOLS which
# may point to the old TheIgors checkout.
_CC_QUEUE = _UU_ROOT / "lab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable  # same venv interpreter that started the daemon

# Tags that indicate a ticket should use the cheap minion model (qwen).
# All other sprint tickets route to worker tier (deepseek-v4-flash).
_MINION_TAGS = frozenset({"minion"})


def _launch_cc_instance(ticket_id: str) -> None:
    """Spawn 'claude -p /sprint-ticket <id>' in a named detached tmux session.

    Session name: cc-{ticket_id}. Checks for an existing session first to
    prevent double-launch. Best-effort — launch failure logs but does not
    block the dispatch return value.
    """
    session = f"cc-{ticket_id}"
    try:
        already = subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        )
        if already.returncode == 0:
            log.info("_launch_cc_instance: session %s already exists — skip", session)
            return
        subprocess.Popen(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session,
                "-c",
                str(_UU_ROOT),
                "--",
                "claude",
                "--dangerously-skip-permissions",
                "-p",
                f"/sprint-ticket {ticket_id}",
            ],
        )
        log.info(
            "_launch_cc_instance: launched CC for %s (session=%s, wd=%s)",
            ticket_id,
            session,
            _UU_ROOT,
        )
    except Exception as e:
        log.warning("_launch_cc_instance: failed to launch CC for %s: %s", ticket_id, e)


def cc_dispatch_fn(ticket: dict) -> bool:
    """Dispatch a ticket to a CC instance.

    1. Marks in_progress via cc_queue.py dispatch.
    2. Posts GRANNY_DISPATCH to the shared channel for observability.
    3. Spawns 'claude -p /sprint-ticket <id>' in a named tmux session.
    """
    ticket_id = ticket.get("id", "")
    if not ticket_id:
        log.warning("cc_dispatch_fn: ticket has no id — skipping")
        return False

    # Mark in_progress via cc_queue
    try:
        result = subprocess.run(
            [_PYTHON, str(_CC_QUEUE), "dispatch", ticket_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            log.warning(
                "cc_queue dispatch %s failed: %s", ticket_id, result.stderr[:200]
            )
    except Exception as e:
        log.warning("cc_dispatch_fn: cc_queue call failed for %s: %s", ticket_id, e)
        # Continue — channel post + CC launch matter more than the queue mark

    # Post to shared channel for observability — best-effort, never blocks launch
    try:
        from unseen_university.channel import post_to_channel

        title = ticket.get("title", "")[:60]
        size = ticket.get("size", "?")
        tags = ",".join(ticket.get("tags", []))
        msg = (
            f"GRANNY_DISPATCH|ticket={ticket_id}|worker=claude|size={size}"
            f"|tags={tags}|title={title}"
        )
        post_to_channel(msg, author="granny-weatherwax", channel="shared")
        log.info("cc_dispatch_fn: channel post OK for %s", ticket_id)
    except Exception as e:
        log.warning("cc_dispatch_fn: channel post failed for %s: %s", ticket_id, e)

    # Launch the CC instance — best-effort, never blocks return value
    _launch_cc_instance(ticket_id)
    return True


def inference_dispatch_fn(ticket: dict) -> bool:
    """Dispatch a ticket to MinionDevice (cheap inference + tool loop).

    Picks task_class from ticket tags:
      'minion' → qwen via OR
      'worker' → deepseek-v4-flash via OR

    Runs synchronously — blocks until the tool loop completes or escalates.
    DONE result  → submits ticket via cc_queue.py done (awaiting_validation).
    ESCALATE     → sets worker=claude + resets to sprint + spawns CC fallback.
                   The set-worker flag prevents Granny from re-routing to minion.
    """
    ticket_id = ticket.get("id", "")
    if not ticket_id:
        log.warning("inference_dispatch_fn: ticket has no id — skipping")
        return False

    tags = set(ticket.get("tags", []))
    task_class = "minion" if (tags & _MINION_TAGS) else "worker"

    # Mark in_progress via cc_queue
    try:
        result = subprocess.run(
            [_PYTHON, str(_CC_QUEUE), "dispatch", ticket_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            log.warning(
                "inference_dispatch %s: cc_queue dispatch failed: %s",
                ticket_id,
                result.stderr[:200],
            )
    except Exception as e:
        log.warning(
            "inference_dispatch_fn: cc_queue call failed for %s: %s", ticket_id, e
        )

    # Post dispatch event to channel
    try:
        from unseen_university.channel import post_to_channel

        title = ticket.get("title", "")[:60]
        size = ticket.get("size", "?")
        tags_str = ",".join(ticket.get("tags", []))
        post_to_channel(
            f"GRANNY_DISPATCH|ticket={ticket_id}|worker={task_class}|size={size}"
            f"|tags={tags_str}|title={title}",
            author="granny-weatherwax",
            channel="shared",
        )
    except Exception as e:
        log.warning(
            "inference_dispatch_fn: channel post failed for %s: %s", ticket_id, e
        )

    # Run the tool loop via MinionDevice
    try:
        from devices.minion.device import MinionDevice
        from devices.minion.shim import WorkerEnvelope

        envelope = WorkerEnvelope(
            ticket_id=ticket_id,
            description=f"Title: {ticket.get('title', '')}\n\n{ticket.get('description', '')}",
            session_id=ticket_id,  # one session per ticket → model affinity in rules engine
            cwd=str(_UU_ROOT),
            task_class=task_class,
        )
        worker_result = MinionDevice().execute(envelope)

        log.info(
            "inference_dispatch %s: signal=%r task_class=%s iterations=%d tools=%s "
            "cost_usd=%.4f in_tok=%d out_tok=%d",
            ticket_id,
            worker_result.signal,
            task_class,
            worker_result.iterations,
            worker_result.tools_called,
            worker_result.cost_usd,
            worker_result.input_tokens,
            worker_result.output_tokens,
        )

        # Post result + cost to channel for observability
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(
                f"MINION_RESULT|ticket={ticket_id}|signal={worker_result.signal}"
                f"|task_class={task_class}|iterations={worker_result.iterations}"
                f"|cost_usd={worker_result.cost_usd:.4f}"
                f"|tokens_in={worker_result.input_tokens}"
                f"|tokens_out={worker_result.output_tokens}",
                author="granny-weatherwax",
                channel="shared",
            )
        except Exception:
            pass

        if worker_result.signal == "DONE":
            summary = f"minion({task_class}): {worker_result.notes[:200]}"
            try:
                subprocess.run(
                    [_PYTHON, str(_CC_QUEUE), "done", ticket_id, summary],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as e:
                log.warning("inference_dispatch %s: done call failed: %s", ticket_id, e)
            return True

        # ESCALATE — mark for CC routing so Granny won't re-dispatch to minion
        log.warning(
            "inference_dispatch %s: escalating to CC — %s: %s",
            ticket_id,
            worker_result.signal,
            worker_result.notes[:200],
        )
        for cmd in (
            [_PYTHON, str(_CC_QUEUE), "set-worker", "claude", ticket_id],
            [_PYTHON, str(_CC_QUEUE), "setstatus", ticket_id, "sprint"],
            [
                _PYTHON,
                str(_CC_QUEUE),
                "log",
                f"ESCALATED from {task_class}: {worker_result.signal} — {worker_result.notes[:300]}",
            ],
        ):
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            except Exception as e:
                log.warning(
                    "inference_dispatch %s: escalation cmd failed: %s", ticket_id, e
                )
        _launch_cc_instance(ticket_id)
        return True

    except Exception as e:
        log.error("inference_dispatch %s: minion execution failed: %s", ticket_id, e)
        return False
