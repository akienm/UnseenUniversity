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
    """Dispatch a ticket to a cheap inference model via InferenceDevice.

    Picks task_class based on ticket tags:
      'minion' for tickets tagged 'minion' → qwen via OR
      'worker' for all other sprint tickets → deepseek-v4-flash via OR

    Runs synchronously on the caller's thread — inference call blocks until
    complete (default 60s timeout). Cost is logged at INFO and posted to channel.
    Ticket is submitted for validation via cc_queue.py done (not auto-closed).
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

    # Build prompt from ticket description
    prompt = (
        "You are a software engineering assistant.\n\n"
        f"Complete the following task:\n\n"
        f"Title: {ticket.get('title', '')}\n\n"
        f"Description:\n{ticket.get('description', '')}"
    )

    # Run inference via InferenceDevice — rules engine selects model by task_class
    try:
        from devices.inference.device import InferenceDevice
        from devices.inference.shim import InferenceRequest

        device = InferenceDevice()
        req = InferenceRequest(
            messages=[{"role": "user", "content": prompt}],
            task_class=task_class,
            agent_id=f"granny-{task_class}",
            instance_id=ticket_id,
            coa_id=task_class,
        )
        resp = device.dispatch(req)

        log.info(
            "inference_dispatch %s: task_class=%s model=%s in_tokens=%d out_tokens=%d"
            " cost_usd=%.6f elapsed_ms=%d",
            ticket_id,
            task_class,
            resp.model,
            resp.input_tokens,
            resp.output_tokens,
            resp.cost_estimate,
            resp.elapsed_ms,
        )

        # Post cost to channel for observability
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(
                f"INFERENCE_COST|ticket={ticket_id}|task_class={task_class}"
                f"|model={resp.model}|in_tokens={resp.input_tokens}"
                f"|out_tokens={resp.output_tokens}|cost_usd={resp.cost_estimate:.6f}",
                author="granny-weatherwax",
                channel="shared",
            )
        except Exception:
            pass

        # Submit for validation — not auto-close; output needs human review
        summary = f"{task_class}({resp.model}): {resp.text[:200].strip()}"
        try:
            subprocess.run(
                [_PYTHON, str(_CC_QUEUE), "done", ticket_id, summary],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            log.warning("inference_dispatch %s: done call failed: %s", ticket_id, e)

    except Exception as e:
        log.error("inference_dispatch %s: inference failed: %s", ticket_id, e)
        return False

    return True
