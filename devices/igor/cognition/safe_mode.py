"""
safe_mode.py — Degraded-safe mode watchdog (T-igor-degrade-safe).

When COA's total stuck-cycle count (across dreaming resets) exceeds
IGOR_DEGRADE_SAFE_THRESHOLD (default 30), trip() is called once:

  1. Writes IGOR_SAFE_MODE=true to igor.switches.cfg (persistent)
  2. Sets os.environ["IGOR_SAFE_MODE"] = "true" (immediate effect)
  3. Posts SAFE_MODE_TRIP to the shared channel for observability

Human-reset-only: the operator removes IGOR_SAFE_MODE=true from
igor.switches.cfg and restarts Igor to clear the flag.

Downstream gates (dreaming writes, action-tools) should check
is_safe_mode() before running. self_edit already gates on
IGOR_SELF_EDIT_ENABLED (unchanged). New action-tool gates are follow-on work.
# tags: Cognition, Safety
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..memory.node_id import _DEFAULT_INSTANCE as _INSTANCE_DEFAULT

_log = logging.getLogger(__name__)

THRESHOLD_DEFAULT = 30
_SAFE_MODE_FLAG = "IGOR_SAFE_MODE"


def is_safe_mode() -> bool:
    """True when IGOR_SAFE_MODE=true is set in the environment."""
    return os.getenv(_SAFE_MODE_FLAG, "false").strip().lower() == "true"


def trip(stuck_cycles: int) -> bool:
    """Activate degraded-safe mode. One-shot: safe to call even if already active.

    Returns True if the trip succeeded (file written + inbox entry appended).
    Returns False on any error — caller should log but not crash.
    """
    try:
        _write_safe_mode_flag()
        _notify_channel(stuck_cycles)
        _log.warning(
            "SAFE_MODE_TRIP: stuck_cycles=%d threshold=%d — safe mode activated; "
            "human reset required (remove IGOR_SAFE_MODE=true from igor.switches.cfg)",
            stuck_cycles,
            THRESHOLD_DEFAULT,
        )
        return True
    except Exception as _e:
        _log.error("SAFE_MODE_TRIP failed: %s", _e)
        return False


def _write_safe_mode_flag() -> None:
    """Append IGOR_SAFE_MODE=true to igor.switches.cfg and set os.environ."""
    try:
        from ..paths import paths as _paths

        switches_cfg: Path = _paths().instance / "igor.switches.cfg"
    except Exception:
        switches_cfg = (
            Path(
                os.getenv("IGOR_RUNTIME_ROOT", str(Path.home() / ".unseen_university"))
            )
            / os.getenv("IGOR_INSTANCE_ID", _INSTANCE_DEFAULT)
            / "igor.switches.cfg"
        )

    switches_cfg.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content; strip any prior IGOR_SAFE_MODE lines so last-wins
    # doesn't accumulate duplicate entries.
    existing = ""
    if switches_cfg.exists():
        existing = switches_cfg.read_text(encoding="utf-8")

    lines = [
        l for l in existing.splitlines() if not l.strip().startswith("IGOR_SAFE_MODE")
    ]
    lines.append(
        "IGOR_SAFE_MODE=true  # written by safe_mode watchdog — human reset required"
    )
    switches_cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Also set in os.environ so the current session sees it immediately.
    os.environ[_SAFE_MODE_FLAG] = "true"


def _notify_channel(stuck_cycles: int) -> None:
    """Post SAFE_MODE_TRIP to the shared channel for observability."""
    try:
        from ..tools.channel_post import post_to_channel

        post_to_channel(
            f"SAFE_MODE_TRIP|stuck_cycles={stuck_cycles}"
            "|action=degraded_safe_mode_activated"
            "|reset=remove_IGOR_SAFE_MODE_from_switches_cfg_and_restart",
        )
    except Exception as _e:
        _log.debug("SAFE_MODE_TRIP channel notify failed (non-fatal): %s", _e)
