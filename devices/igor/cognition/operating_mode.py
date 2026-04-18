"""
operating_mode.py — T-igor-modes

Four biological operating modes derived from milieu state + input recency.
Not a flag — an emergent property. The mode gates which subsystems are
active and how aggressively resources are used.

Modes (mapped to neuroscience):
  FOREGROUND  — Active conversation, directed work. High arousal, full
                cognition, cloud fallback. Gamma/beta equivalent.
  DEFAULT     — No active conversation. Self-referential thinking,
                spontaneous association, creative recombination. The
                shower insight mode. Alpha equivalent.
  CONSOLIDATION — Sleep/NREM. Mechanical memory maintenance: replay,
                  merge, prune, adopt. Slow wave equivalent.
  DREAMING    — Sleep/REM. Creative recombination, random-seed
                spreading activation, emotional memory processing.
                Theta equivalent.

Transitions are gradient via milieu arousal + input recency + clock.
"""

import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)


class Mode(Enum):
    FOREGROUND = "foreground"
    DEFAULT = "default"
    CONSOLIDATION = "consolidation"
    DREAMING = "dreaming"


# ── Configuration ────────────────────────────────────────────────────────────

# How long since last user input before transitioning out of FOREGROUND
_FOREGROUND_TIMEOUT_SEC = float(os.getenv("IGOR_FOREGROUND_TIMEOUT", "300"))  # 5 min

# How long idle before transitioning from DEFAULT to sleep
_SLEEP_IDLE_SEC = float(os.getenv("IGOR_SLEEP_IDLE_SEC", "1200"))  # 20 min

# Sleep window (clock-gated)
_SLEEP_WINDOW_START = int(os.getenv("IGOR_BENCHMARK_WINDOW_START", "22"))  # 10 PM
_SLEEP_WINDOW_END = int(os.getenv("IGOR_BENCHMARK_WINDOW_END", "6"))  # 6 AM

# Dreaming phase duration within sleep (cycles)
_DREAM_CYCLE_SEC = float(os.getenv("IGOR_DREAM_CYCLE_SEC", "600"))  # 10 min


def _in_sleep_window() -> bool:
    """Check if current time is within the sleep window."""
    hour = datetime.now().hour
    if _SLEEP_WINDOW_START > _SLEEP_WINDOW_END:
        # Wraps midnight: e.g. 22-6
        return hour >= _SLEEP_WINDOW_START or hour < _SLEEP_WINDOW_END
    return _SLEEP_WINDOW_START <= hour < _SLEEP_WINDOW_END


def _seconds_since_last_input(last_input_ts: float) -> float:
    """Seconds since last user input."""
    if last_input_ts <= 0:
        return float("inf")
    return time.monotonic() - last_input_ts


def derive_mode(
    last_input_ts: float,
    arousal: float = 0.0,
    sleep_start_ts: float = 0.0,
) -> Mode:
    """
    Derive the current operating mode from milieu state + input recency.

    Args:
        last_input_ts: monotonic timestamp of last user interaction
        arousal: current milieu arousal (-1 to +1)
        sleep_start_ts: monotonic timestamp when sleep phase began (0 if not sleeping)

    Returns the current Mode.
    """
    idle_sec = _seconds_since_last_input(last_input_ts)

    # FOREGROUND: recent input + high arousal
    if idle_sec < _FOREGROUND_TIMEOUT_SEC:
        return Mode.FOREGROUND

    # Sleep modes: clock-gated OR long idle
    if _in_sleep_window() or idle_sec > _SLEEP_IDLE_SEC:
        # Within sleep: cycle between CONSOLIDATION and DREAMING
        if sleep_start_ts > 0:
            sleep_elapsed = time.monotonic() - sleep_start_ts
            # Cycle: consolidation for most of the time, dreaming in bursts
            cycle_pos = sleep_elapsed % (_DREAM_CYCLE_SEC * 2)
            if cycle_pos > _DREAM_CYCLE_SEC * 1.5:
                return Mode.DREAMING
        return Mode.CONSOLIDATION

    # DEFAULT: not in foreground, not sleeping
    return Mode.DEFAULT


# ── Mode properties ──────────────────────────────────────────────────────────

_MODE_CONFIG = {
    Mode.FOREGROUND: {
        "cloud_allowed": True,
        "twm_behavior": "hot",  # everything competing
        "response_expected": True,
        "push_source_tier": "all",
    },
    Mode.DEFAULT: {
        "cloud_allowed": False,
        "twm_behavior": "decay",  # natural decay
        "response_expected": False,
        "push_source_tier": "creative",  # curiosity, self-observation
    },
    Mode.CONSOLIDATION: {
        "cloud_allowed": False,
        "twm_behavior": "integrate",  # flush + full integration pass
        "response_expected": False,
        "push_source_tier": "maintenance",  # consolidation, pruning
    },
    Mode.DREAMING: {
        "cloud_allowed": False,
        "twm_behavior": "random",  # random-seed spreading activation
        "response_expected": False,
        "push_source_tier": "creative",  # surprising connections
    },
}


def mode_config(mode: Mode) -> dict:
    """Return configuration dict for the given mode."""
    return dict(_MODE_CONFIG.get(mode, _MODE_CONFIG[Mode.DEFAULT]))
