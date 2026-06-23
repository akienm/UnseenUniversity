"""Out-of-band tmux nag for new/reopened system alarms (T-system-alarms-tmux-nag).

A worker heads-down in a tmux session won't see the web ALARMS PANEL. This is
the in-session nudge: a sweep (run periodically, out of the ``raise_alarm`` hot
path) that nags ONCE per new/reopened alarm — never per dedup increment — and
points the worker at ``uu alarms``.

"New/reopened" = an open alarm without a ``notified_at`` stamp. After a
successful nag the alarm is stamped (``mark_notified``), so increments don't
re-nag; a reopened alarm is recreated without the stamp, so it does. Fail-soft:
a send failure or absent tmux session is a silent no-op (no stamp, so it retries
when a session appears) and never raises into the caller.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional

from . import system_alarms as sa

log = logging.getLogger("unseen_university.system_alarm_notifier")


def _tmux_nag(summary: str, session: "Optional[str]") -> bool:
    """Send a LOUD nag to the tmux session (3× Enter then the text, not submitted).

    Mirrors NotificationDispatcher._tmux_interrupt. Returns False when the
    session is absent/unreachable or the send fails — never raises.
    """
    if not session:
        return False
    try:
        if subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        ).returncode != 0:
            return False
        for _ in range(3):
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "", "Enter"],
                capture_output=True,
                check=True,
            )
        subprocess.run(
            ["tmux", "send-keys", "-t", session, f"[alarm] {summary}", ""],
            capture_output=True,
            check=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-soft delivery
        log.warning("alarm nag: tmux send failed: %s", exc)
        return False


def notify_new_alarms(
    *,
    send_fn: "Optional[Callable[[str], bool]]" = None,
    tmux_session: "Optional[str]" = None,
    now: "Optional[datetime]" = None,
) -> int:
    """Nag once per new/reopened alarm; return the number nagged. Never raises.

    Out-of-band — call from a periodic sweep, not from ``raise_alarm``. Only
    stamps ``notified_at`` on a SUCCESSFUL send, so an absent tmux session leaves
    the alarm un-nagged for the next sweep instead of silently swallowing it.

    Args:
        send_fn: injectable sender ``(summary) -> bool`` (tests pass a fake);
            defaults to the tmux nag against ``tmux_session``/``CC_TMUX_SESSION``.
        tmux_session: tmux session name (defaults to ``CC_TMUX_SESSION`` env).
        now: injectable clock for the stamp.
    """
    now = now or datetime.now(timezone.utc)
    session = tmux_session or os.environ.get("CC_TMUX_SESSION")
    sender = send_fn or (lambda summary: _tmux_nag(summary, session))
    nagged = 0
    for rec in sa.list_alarms():
        try:
            if rec.get("notified_at"):
                continue  # already nagged — an increment, not new/reopened
            sig = rec.get("signature")
            if not sig:
                continue
            summary = f"SYSTEM ALARM: {sig} ({rec.get('count', 0)}×) — run: uu alarms"
            if sender(summary):
                sa.mark_notified(sig, now=now)
                nagged += 1
        except Exception as exc:  # noqa: BLE001 — one bad alarm must not stop the sweep
            log.error("alarm nag: failed for signature=%s: %s", rec.get("signature"), exc)
    return nagged
