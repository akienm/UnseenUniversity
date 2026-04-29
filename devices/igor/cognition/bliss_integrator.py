"""
bliss_integrator.py — slow EMA over Pursuit completion events.

Biology: the μ-opioid "liking" signal is slow and separable from the
phasic dopamine "wanting" signal. Pursuits already emit the wanting side
(commitment + completion dopamine). Bliss is the liking counterpart — a
slow integrator that accumulates from completion events and decays when
nothing is getting done.

Feedback: when bliss is high, milieu gets a valence floor-lift and a
dominance baseline bump — the behavioral analog of "the system wants you
to do more of that."

Scope (T-bliss-integrator, Stage 1):
  - Subscribe to pursuit DopamineEvent stream.
  - EMA on completion events (magnitude added, sustained window decay).
  - get_bliss() -> float in [0, 1].
  - Apply-to-milieu hook (called periodically by milieu or a daemon).

Deferred to Stage 2 (gated on T-goal-formation-from-conversation):
  - Weight completions by alignment with persistent goals, so bliss
    reflects *meaningful* completion, not completion volume alone.

Gate: IGOR_BLISS_ENABLED (default false). When disabled, get_bliss()
returns 0.0 and apply-to-milieu is a no-op.

See lab/design_docs/pursuit_layer.md for the design conversation this
derives from.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import pursuits as _pursuits
from ..igor_base import IgorBase

log = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("IGOR_BLISS_ENABLED", "false").lower() == "true"


# Time constant for exponential decay of bliss when no completions fire.
# 30 minutes: at t=WINDOW, the contribution of a past event has decayed
# to ~37%. At t=2*WINDOW, ~14%. At t=3*WINDOW, ~5%.
DEFAULT_WINDOW_SECS = float(os.getenv("IGOR_BLISS_WINDOW_SECS", "1800"))

# Cap on accumulated bliss — otherwise a burst of completions could
# saturate the signal. Biology: liking has a ceiling; habituation dampens
# repeated rewards.
DEFAULT_MAX_BLISS = 1.0

# Magnitude applied to the valence floor when bliss is 1.0. The effect on
# milieu is: valence cannot fall below bliss_level * FLOOR_LIFT. Chosen
# small so bliss is a slow background tilt, not a mood override.
DEFAULT_FLOOR_LIFT = 0.2

# Magnitude applied to dominance when bliss is 1.0. A small baseline bump.
DEFAULT_DOMINANCE_BUMP = 0.15


@dataclass
class BlissState:
    level: float = 0.0
    last_update_ts: float = field(default_factory=time.time)
    event_count: int = 0  # lifetime, for observability


class BlissIntegrator(IgorBase):
    """In-process EMA over pursuit completion events.

    Single process-wide instance; `get()` returns the module-level
    integrator. Subscribed to the pursuits registry on first access.
    """

    def __init__(
        self,
        window_secs: float = DEFAULT_WINDOW_SECS,
        max_bliss: float = DEFAULT_MAX_BLISS,
    ) -> None:
        super().__init__()
        self.window_secs = window_secs
        self.max_bliss = max_bliss
        self.state = BlissState()
        self._subscribed = False

    def _decay_to_now(self, now: Optional[float] = None) -> None:
        """Apply exponential decay from last_update_ts to `now`."""
        now = now if now is not None else time.time()
        dt = max(0.0, now - self.state.last_update_ts)
        if dt <= 0.0:
            return
        # Exponential decay with time constant = window_secs.
        # Equivalent to EMA where contribution of old value shrinks toward 0.
        decay = math.exp(-dt / self.window_secs)
        self.state.level *= decay
        self.state.last_update_ts = now

    def on_pursuit_event(self, event: _pursuits.DopamineEvent) -> None:
        """Subscriber callback. Only completion events contribute."""
        if not enabled():
            return
        if event.kind != "completion":
            return
        self._decay_to_now(event.ts)
        # Add completion magnitude, clamp to max.
        self.state.level = min(self.max_bliss, self.state.level + event.magnitude)
        self.state.event_count += 1
        log.debug(
            "bliss += %.2f (event=%s) → level=%.3f",
            event.magnitude,
            event.note,
            self.state.level,
        )

    def get_bliss(self) -> float:
        """Return current bliss level [0, max_bliss], applying decay first."""
        if not enabled():
            return 0.0
        self._decay_to_now()
        return self.state.level

    def ensure_subscribed(self) -> None:
        if self._subscribed:
            return
        _pursuits.registry().subscribe(self.on_pursuit_event)
        self._subscribed = True

    def apply_to_milieu(
        self,
        milieu,
        floor_lift: float = DEFAULT_FLOOR_LIFT,
        dominance_bump: float = DEFAULT_DOMINANCE_BUMP,
    ) -> None:
        """Nudge milieu based on current bliss level. Non-fatal on failure.

        milieu.ingest_bliss_lift(level, floor_lift, dominance_bump) is the
        expected interface; if not available (older milieu), logs and returns.
        """
        if not enabled():
            return
        level = self.get_bliss()
        if level <= 0.0:
            return
        try:
            milieu.ingest_bliss_lift(level, floor_lift, dominance_bump)
        except AttributeError:
            log.info("milieu has no ingest_bliss_lift method — bliss feedback skipped")
        except Exception as exc:
            log.info("bliss feedback to milieu failed: %s", exc)

    def reset(self) -> None:
        """Test helper — wipe integrator state."""
        self.state = BlissState()


_integrator: Optional[BlissIntegrator] = None


def get() -> BlissIntegrator:
    """Return the process-wide BlissIntegrator, subscribing on first use."""
    global _integrator
    if _integrator is None:
        _integrator = BlissIntegrator()
    _integrator.ensure_subscribed()
    return _integrator


def reset_for_test() -> None:
    """Test helper — drop the module-level integrator so tests start fresh."""
    global _integrator
    _integrator = None
