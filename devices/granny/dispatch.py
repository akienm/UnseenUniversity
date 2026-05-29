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
from pathlib import Path

log = logging.getLogger(__name__)

_CC_QUEUE = (
    Path(os.environ.get("CC_WORKFLOW_TOOLS", Path.home() / "TheIgors/lab/claudecode"))
    / "cc_queue.py"
)

# Repo root — where CC must run so it picks up CLAUDE.md and project context.
_UU_ROOT = Path(__file__).parent.parent.parent.resolve()


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
            ["python3", str(_CC_QUEUE), "dispatch", ticket_id],
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

    # Post to shared channel for observability
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
        log.info("cc_dispatch_fn: dispatched %s to CC via channel", ticket_id)
    except Exception as e:
        log.error("cc_dispatch_fn: channel post failed for %s: %s", ticket_id, e)
        return False

    # Launch the CC instance — best-effort, never blocks return value
    _launch_cc_instance(ticket_id)
    return True
