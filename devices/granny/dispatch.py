"""
dispatch.py — Dispatch functions for GrannyWeatherwaxDevice routing edges.

Each dispatch_fn takes a ticket dict and returns bool (True=dispatched).
The CC dispatch function:
  1. Calls cc_queue.py dispatch <ticket_id> to set the ticket in_progress
  2. Posts GRANNY_DISPATCH to the shared channel so CC task listener picks it up
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_CC_QUEUE = Path(os.environ.get("CC_WORKFLOW_TOOLS", Path.home() / "TheIgors/lab/claudecode")) / "cc_queue.py"


def cc_dispatch_fn(ticket: dict) -> bool:
    """Dispatch a ticket to a CC instance via the shared channel.

    Marks the ticket in_progress via cc_queue.py dispatch, then posts a
    GRANNY_DISPATCH message to the shared channel so the CC task listener
    (T-cc-task-listener) can pick it up and auto-sprint it.
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
            log.warning("cc_queue dispatch %s failed: %s", ticket_id, result.stderr[:200])
    except Exception as e:
        log.warning("cc_dispatch_fn: cc_queue call failed for %s: %s", ticket_id, e)
        # Continue — the channel post is more important than the queue update

    # Post to shared channel for CC task listener
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
        return True
    except Exception as e:
        log.error("cc_dispatch_fn: channel post failed for %s: %s", ticket_id, e)
        return False
