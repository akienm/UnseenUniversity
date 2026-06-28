"""
DeviceConfig — per-device policy dataclass + runtime path helpers.

Rack default is drop-oldest: agent traffic is typically state/status updates
where newer supersedes older. Set drop_newest=True for order-preserving
pipelines (e.g. a command queue where sequence matters).

All defaults are rack-level sensible. Override per device at registration time.

Runtime path helpers
--------------------
unseen_university_home() → ~/.unseen_university/ (or $UNSEEN_UNIVERSITY_HOME)
unseen_university_logs() → $UNSEEN_UNIVERSITY_HOME/logs/

Set UNSEEN_UNIVERSITY_HOME to relocate the entire runtime tree (CI, multi-user,
non-home mounts). Default is ~/.unseen_university/ for single-user desktop use.
"""

import os
from dataclasses import asdict as _asdict
from dataclasses import dataclass
from pathlib import Path


def unseen_university_home() -> Path:
    """Root of the unseen_university runtime tree."""
    return Path(
        os.environ.get(
            "UNSEEN_UNIVERSITY_HOME",
            str(Path.home() / ".unseen_university"),
        )
    )


def unseen_university_logs() -> Path:
    """Root of the hierarchical log tree: $UNSEEN_UNIVERSITY_HOME/logs/"""
    return unseen_university_home() / "logs"


@dataclass
class DeviceConfig:
    # Queue overflow — rack default: drop oldest
    max_queue_length: int = 100
    drop_newest: bool = False  # True = drop-newest (order-preserving mode)

    # Restart loop protection
    max_restart_failures: int = 3
    restart_window_seconds: int = 60
    restart_backoff_seconds: float = 5.0

    # Gate: when True the rack will never auto-unblock this device after
    # a restart-loop failure — only a manual operator ungate clears it.
    manual_block_only: bool = False

    def to_dict(self) -> dict:
        return _asdict(self)


# Rack-level retention policy. All mailboxes retain messages for this many hours
# regardless of SEEN status. After expiry, messages are expunged permanently.
RETENTION_HOURS: int = 24
