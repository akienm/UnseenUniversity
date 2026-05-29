"""
dispatch.py — Dispatch functions for GrannyWeatherwaxDevice routing edges.

Each dispatch_fn takes a ticket dict and returns bool (True=dispatched).
The CC dispatch function:
  1. Calls cc_queue.py dispatch <ticket_id> to set the ticket in_progress
  2. Posts GRANNY_DISPATCH to the shared channel for observability
  3. Spawns a detached tmux session running 'claude -p /sprint-ticket <id>'
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
