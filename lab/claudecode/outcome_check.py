#!/usr/bin/env python3
"""outcome_check.py — print /outcome prompt when a decision's last ticket closes.

Called by sprint-ticket Step 11 after closing a ticket. Reads the ticket's
decision_id, looks up spawned_tickets in adc.palace, checks all are closed in
the queue. Prints nothing when the decision is not fully shipped yet.

Usage:
    python3 lab/claudecode/outcome_check.py T-my-ticket
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

_DB_URL = os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
_CC_WORKFLOW_TOOLS = os.environ.get("CC_WORKFLOW_TOOLS", str(_UU_ROOT / "lab" / "claudecode"))


def _ticket_decision_id(ticket_id: str) -> str | None:
    """Return the decision_id field from the queue for ticket_id, or None."""
    import subprocess
    result = subprocess.run(
        [sys.executable, f"{_CC_WORKFLOW_TOOLS}/cc_queue.py", "show", ticket_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data.get("decision_id") or None
    except (json.JSONDecodeError, KeyError):
        return None


def _decision_palace_node(decision_id: str) -> dict | None:
    """Return the palace node for the decision, or None."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT path, title, content, metadata FROM adc.palace WHERE path = %s",
                (f"palace.decisions.{decision_id}",),
            )
            row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _all_closed(ticket_ids: list[str]) -> bool:
    """Return True if every ticket in the list is closed in the queue."""
    import subprocess
    for tid in ticket_ids:
        result = subprocess.run(
            [sys.executable, f"{_CC_WORKFLOW_TOOLS}/cc_queue.py", "show", tid],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
            status = data.get("status", "")
            if status not in ("closed", "cancelled", "done"):
                return False
        except (json.JSONDecodeError, KeyError):
            return False
    return True


def _extract_hypothesis(content: str) -> str:
    """Extract the ## Hypothesis section text from decision content."""
    m = re.search(r"## Hypothesis\s*\n(.+?)(?:\n##|\Z)", content, re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    return ""


def check(ticket_id: str) -> None:
    decision_id = _ticket_decision_id(ticket_id)
    if not decision_id:
        return

    node = _decision_palace_node(decision_id)
    if not node:
        return

    metadata = node.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return

    spawned = metadata.get("spawned_tickets") or []
    if not spawned:
        return

    if not _all_closed(spawned):
        return

    hypothesis = _extract_hypothesis(node.get("content", ""))
    hypothesis_line = f": {hypothesis}" if hypothesis else ""
    print(
        f"\n🏁 Decision {decision_id} is fully shipped"
        f" — run /outcome {decision_id}{hypothesis_line}"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <ticket-id>")
        sys.exit(1)
    check(sys.argv[1])
