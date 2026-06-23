"""Out-of-band tmux nag for new/reopened system alarms.

STUB (proof scaffold for T-system-alarms-tmux-nag) — ``notify_new_alarms``
returns 0 so the proof test fails on a behavioral assertion (no nag fired), not
a missing symbol. Real sweep lands in the next commit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("unseen_university.system_alarm_notifier")


def notify_new_alarms(
    *,
    send_fn: "Optional[Callable[[str], bool]]" = None,
    tmux_session: "Optional[str]" = None,
    now: "Optional[datetime]" = None,
) -> int:
    """STUB — real out-of-band nag sweep in the next commit."""
    return 0
