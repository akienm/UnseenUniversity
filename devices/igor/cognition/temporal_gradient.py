"""
TemporalGradient — unified decay primitive (T-temporal-gradient).

Replaces 6 special-cased decay implementations scattered across the codebase.
All follow the same pattern: value × 0.5^(elapsed / half_life).

Currently wired into:
  - cortex.py tails heat (TAIL_GRADIENT)

Pending migrations (same pattern, different parameters):
  - basal_ganglia.py compute_decay_factor()  — math.exp(-days/tau), adaptive τ
  - response_habituation.py decay_factor()   — count-based habituation
  - milieu.py natural decay                  — ×0.98 per timer tick
  - push_sources.py twm_decay_attractor()    — ×0.90 per heartbeat (60s)
  - narrative_engine.py twm_decay_slot()     — ×0.70 per NE cycle

Usage:
    from devices.igor.cognition.temporal_gradient import TemporalGradient

    g = TemporalGradient(half_life_hours=24.0)
    factor = g.factor(elapsed_hours=12.0)    # → 0.5  (half gone after 12h)
    heat   = g.apply(1.0, elapsed_hours=6.0) # → 0.707...
    factor = g.factor_for(recorded_at)       # → computed from datetime to now
"""

from __future__ import annotations

import math
from datetime import datetime
from ..igor_base import IgorBase


class TemporalGradient(IgorBase):
    """
    Exponential decay with a configurable half-life.

    Formula: factor = 0.5 ^ (elapsed_hours / half_life_hours)

    Equivalent to: factor = exp(-elapsed_hours × ln(2) / half_life_hours)
    Both forms are identical; the 0.5^ form is used here for readability.
    """

    def __init__(self, half_life_hours: float):
        if half_life_hours <= 0:
            raise ValueError(f"half_life_hours must be > 0, got {half_life_hours}")
        self.half_life_hours = half_life_hours

    def factor(self, elapsed_hours: float) -> float:
        """Decay factor in [0, 1] for a given elapsed time.

        elapsed_hours=0          → 1.0 (no decay)
        elapsed_hours=half_life  → 0.5 (half gone)
        elapsed_hours → inf      → 0.0 (fully decayed)
        """
        return 0.5 ** (max(0.0, elapsed_hours) / self.half_life_hours)

    def apply(self, value: float, elapsed_hours: float) -> float:
        """Apply decay to a value: value × factor(elapsed_hours)."""
        return value * self.factor(elapsed_hours)

    def factor_for(self, recorded_at: datetime, now: datetime | None = None) -> float:
        """Compute decay factor from a recorded_at datetime to now (or a given now)."""
        now = now or datetime.now()
        elapsed_hours = max(0.0, (now - recorded_at).total_seconds() / 3600.0)
        return self.factor(elapsed_hours)

    def apply_for(
        self, value: float, recorded_at: datetime, now: datetime | None = None
    ) -> float:
        """Apply decay to a value based on age of a recorded_at datetime."""
        return value * self.factor_for(recorded_at, now)

    @classmethod
    def from_half_life_days(cls, days: float) -> "TemporalGradient":
        """Convenience constructor for day-scale half-lives."""
        return cls(half_life_hours=days * 24.0)

    @classmethod
    def from_tick(
        cls, factor_per_tick: float, tick_seconds: float
    ) -> "TemporalGradient":
        """
        Construct from a per-tick multiplicative factor and tick interval.

        Example: milieu decays x0.98 every 60s tick:
            TemporalGradient.from_tick(0.98, 60)

        Example: attractor decays x0.90 every 60s heartbeat:
            TemporalGradient.from_tick(0.90, 60)
        """
        if factor_per_tick <= 0 or factor_per_tick >= 1:
            raise ValueError(
                f"factor_per_tick must be in (0, 1), got {factor_per_tick}"
            )
        # half_life in seconds: factor^(t/tick) = 0.5 => t = tick x log(0.5)/log(factor)
        half_life_seconds = tick_seconds * math.log(0.5) / math.log(factor_per_tick)
        return cls(half_life_hours=half_life_seconds / 3600.0)

    def __repr__(self) -> str:
        return f"TemporalGradient(half_life_hours={self.half_life_hours})"


# ── Pre-built instances for known use cases ─────────────────────────────────

# Tail heat: activation warmth decays over 24 hours
TAIL_GRADIENT = TemporalGradient(half_life_hours=24.0)

# Milieu natural decay: x0.98 per 60s tick -> ~56 min half-life
MILIEU_GRADIENT = TemporalGradient.from_tick(0.98, 60)

# TWM attractor decay: x0.90 per 60s heartbeat -> ~10 min half-life
ATTRACTOR_GRADIENT = TemporalGradient.from_tick(0.90, 60)

# TWM slot decay: x0.70 per NE cycle (assumed ~60s) -> ~3 min half-life
SLOT_GRADIENT = TemporalGradient.from_tick(0.70, 60)

# Habit score decay: base half-life 30 days (activation_count scales tau in BG)
HABIT_GRADIENT = TemporalGradient.from_half_life_days(30.0)
