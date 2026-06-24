"""
cc_task_listener.py — CC factory task listener.

Polls channel_messages for GRANNY_DISPATCH messages and marks the referenced
tickets in_progress via cc_queue.py dispatch. This bridges the Granny routing
step and the CC sprint step.

Protocol (shared channel):
  Granny posts:   GRANNY_DISPATCH|ticket=T-xxx|worker=claude|size=S|tags=Platform|title=...
  Listener sees it, dispatches, posts back:
                  GRANNY_ACK|ticket=T-xxx|status=in_progress

The listener tracks a high-water mark (last processed message id) so it never
double-dispatches. Dispatch is idempotent — cc_queue accepts sprint→in_progress
only, so re-dispatch of an already in_progress ticket is a no-op error logged
at WARNING.

Usage:
    python3 cc_task_listener.py [--once]  # poll until Ctrl-C; --once for single cycle

Embedding in a loop:
    from devlab.claudecode.cc_task_listener import TaskListener
    listener = TaskListener()
    listener.poll_once()       # call from your scheduler
"""

from __future__ import annotations
from unseen_university.identity import home_db_url
from unseen_university._uu_root import uu_home

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CC_QUEUE = Path(__file__).parent / "cc_queue.py"
_HWM_FILE = (
    Path(uu_home())
    / "cc_task_listener_hwm.txt"
)
_POLL_INTERVAL = int(os.environ.get("CC_TASK_LISTENER_INTERVAL", "15"))

_DISPATCH_PREFIX = "GRANNY_DISPATCH"
_ACK_PREFIX = "GRANNY_ACK"
_CHANNEL = "shared"


def _parse_dispatch_msg(content: str) -> dict | None:
    """Parse a GRANNY_DISPATCH|ticket=T-xxx|... message. Returns dict or None."""
    if not content.startswith(_DISPATCH_PREFIX + "|"):
        return None
    parts = {}
    for part in content.split("|")[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()
    return parts if parts.get("ticket") else None


def _read_hwm() -> int:
    """Read high-water mark message ID from state file. Returns 0 if missing."""
    try:
        return int(_HWM_FILE.read_text().strip())
    except Exception:
        return 0


def _write_hwm(hwm: int) -> None:
    _HWM_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HWM_FILE.write_text(str(hwm))


def _fetch_new_messages(since_id: int) -> list[dict]:
    """Return GRANNY_DISPATCH messages with id > since_id from channel_messages."""
    try:
        import psycopg2

        conn = psycopg2.connect(home_db_url())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content FROM infra.channel_messages"
                " WHERE channel = %s AND id > %s"
                " AND content LIKE %s"
                " ORDER BY id",
                (_CHANNEL, since_id, f"{_DISPATCH_PREFIX}|%"),
            )
            rows = [{"id": r[0], "content": r[1]} for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        log.warning("cc_task_listener: DB fetch failed: %s", exc)
        return []


def _dispatch_ticket(ticket_id: str) -> bool:
    """Call cc_queue.py dispatch <ticket_id>. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "python3",
                str(_CC_QUEUE),
                "dispatch",
                ticket_id,
                "--by",
                "granny-weatherwax",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            log.info("cc_task_listener: dispatched %s", ticket_id)
            return True
        log.warning(
            "cc_task_listener: dispatch %s failed (rc=%d): %s",
            ticket_id,
            result.returncode,
            result.stderr[:200],
        )
        return False
    except Exception as exc:
        log.warning("cc_task_listener: dispatch %s error: %s", ticket_id, exc)
        return False


def _post_ack(ticket_id: str, status: str) -> None:
    """Post GRANNY_ACK back to the shared channel."""
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(
            f"{_ACK_PREFIX}|ticket={ticket_id}|status={status}",
            author="cc-task-listener",
            channel=_CHANNEL,
        )
    except Exception as exc:
        log.warning("cc_task_listener: ack post failed: %s", exc)


class TaskListener:
    """Polls shared channel for GRANNY_DISPATCH messages and dispatches tickets."""

    def __init__(self) -> None:
        import threading

        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the run() loop to exit on next sleep boundary."""
        self._stop_event.set()

    def poll_once(self) -> int:
        """Run one poll cycle. Returns count of tickets dispatched."""
        hwm = _read_hwm()
        messages = _fetch_new_messages(hwm)
        dispatched = 0
        new_hwm = hwm

        for msg in messages:
            new_hwm = max(new_hwm, msg["id"])
            parsed = _parse_dispatch_msg(msg["content"])
            if not parsed:
                continue
            ticket_id = parsed["ticket"]
            ok = _dispatch_ticket(ticket_id)
            status = "in_progress" if ok else "dispatch_failed"
            _post_ack(ticket_id, status)
            if ok:
                dispatched += 1
                log.info("cc_task_listener: %s → in_progress", ticket_id)

        if new_hwm > hwm:
            _write_hwm(new_hwm)

        return dispatched

    def run(self) -> None:
        """Poll until stop() is called or KeyboardInterrupt."""
        log.info("cc_task_listener: starting (poll_interval=%ds)", _POLL_INTERVAL)
        while not self._stop_event.is_set():
            try:
                n = self.poll_once()
                if n:
                    log.info("cc_task_listener: %d ticket(s) dispatched", n)
            except Exception as exc:
                log.error("cc_task_listener: poll error: %s", exc)
            self._stop_event.wait(_POLL_INTERVAL)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    once = "--once" in sys.argv
    listener = TaskListener()
    if once:
        n = listener.poll_once()
        print(f"cc_task_listener: {n} ticket(s) dispatched")
    else:
        listener.run()
