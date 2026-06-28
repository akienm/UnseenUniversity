"""
Shared escalation primitive for Igor cognition subsystems.

When a subsystem exhausts its options, escalate_to_channel() is the
default fallback — post a structured missing-info message to the shared
channel so the problem is visible rather than silently dropped.

D-escalate-as-default-2026-05-10: escalate is the third path between
"go mute" and "confabulate". Every subsystem that bottoms out should
call escalate_to_channel() rather than returning silently.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def escalate_to_channel(
    msg: str,
    dedup_key: str | None = None,
    watch_condition: str | None = None,
) -> None:
    """Post an escalation message to the shared channel.

    Wraps the shared channel_post primitive with a consistent author tag
    so escalation messages are distinguishable from normal Igor output.
    Also adds the problem to instance.watch_problems so the lever watcher
    can resurface it when conditions change (D-escalate-as-default-2026-05-10).
    Failures are swallowed — escalation must never crash the caller.
    """
    try:
        from ..tools.channel_post import post_to_channel as _post

        _post(msg, author="igor", channel="shared", dedup_key=dedup_key)
    except Exception as _e:
        log.warning("escalate_to_channel failed: %s", _e)

    # Grand escalation: park the problem on the per-instance watch list
    # so the lever watcher can resurface it when conditions change.
    try:
        from .watch_problems import add_watch_problem

        add_watch_problem(
            problem=msg[:400],
            watch_condition=watch_condition or dedup_key,
        )
    except Exception as _e:
        log.debug("escalate_to_channel: watch_problems deposit failed: %s", _e)
