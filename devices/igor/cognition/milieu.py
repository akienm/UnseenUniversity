"""
Milieu — ambient emotional state.

A slow-drifting 3-dimensional vector (valence, arousal, dominance) that
persists across sessions and shapes habit sensitivity, threshold X, and
the NE's self-narrative. It is NOT per-interaction — it is the background
emotional weather that individual interactions push against.

Key properties:
  - Asymmetric EMA: fast rise (α=0.25), slow fall (α=0.05)
  - Natural decay: all dims drift toward neutral each timer tick (×0.98)
  - Persisted to JSON so mood survives restarts
  - Always in TWM as low-salience ambient context (pushed by MilieuSource)
  - MilieuInterruptor fires on extremes (arousal spike, sustained negative valence)

Consumers:
  - NarrativeEngine reads MOOD_STATE TWM obs and can generate impulses
  - Future: basal_ganglia habit scoring weighted by arousal/dominance
  - Future: threshold X for escalation shaped by dominance (low dominance → escalate sooner)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────

ALPHA_UP   = 0.25   # fast rise toward new signal
ALPHA_DOWN = 0.05   # slow fall away from signal
DECAY      = 0.98   # passive drift toward neutral per tick() call
PUSH_DELTA = 0.08   # min per-dim change required to push a TWM update

# NE's self-assessment is a softer hint than direct interaction signals
NE_ALPHA_UP   = 0.10
NE_ALPHA_DOWN = 0.03


# ── State dataclass ────────────────────────────────────────────────────────────

@dataclass
class MilieuState:
    """
    Three-dimensional affect vector.

    valence   [-1, 1]  pleasant / unpleasant
    arousal   [-1, 1]  activated / deactivated  (negative = tired/calm)
    dominance [-1, 1]  in-control / overwhelmed

    tick counts mutations (debugging/rate-limiting).
    last_update is unix timestamp of last mutation.
    """
    valence:     float = 0.0
    arousal:     float = 0.0
    dominance:   float = 0.3   # start slight positive (default competent)
    tick:        int   = 0
    last_update: float = 0.0


# ── Core Milieu class ──────────────────────────────────────────────────────────

class Milieu:
    """
    Ambient emotional state manager.
    One instance per Igor process (module singleton via init()/get()).
    """

    def __init__(self, instance_id: str):
        self._instance_id = instance_id
        self._path = (
            Path(os.path.expanduser("~/.TheIgors"))
            / f"igor_{instance_id}"
            / "milieu.json"
        )
        self._state = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> MilieuState:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return MilieuState(**{k: v for k, v in data.items()
                                      if k in MilieuState.__dataclass_fields__})
        except Exception:
            pass  # Corrupt or missing — start fresh
        return MilieuState()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(asdict(self._state), indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # Never crash — milieu is advisory

    # ── Math ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _blend(current: float, signal: float,
               alpha_up: float = ALPHA_UP,
               alpha_down: float = ALPHA_DOWN) -> float:
        """Asymmetric EMA: fast rise, slow fall."""
        alpha = alpha_up if signal > current else alpha_down
        return current + alpha * (signal - current)

    @staticmethod
    def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, valence: float, friction: float, roi: float = 0.0) -> MilieuState:
        """
        Ingest one interaction's emotional signals and update the milieu.

        valence  [-1,1]  — direct from pfc.assess_valence()
        friction [0,1]   — from pfc.measure_friction(); high = stressed/activated
        roi      [-1,1]  — from pfc.calculate_roi(); positive = successful/in-control
        """
        s = self._state

        # Valence dimension: direct mapping
        s.valence = self._clamp(self._blend(s.valence, valence))

        # Arousal dimension: friction drives activation (high friction = high arousal)
        # friction is [0,1]; map to [-1,1] by (friction * 2 - 1) then blend
        arousal_signal = self._clamp(friction * 2.0 - 1.0)
        s.arousal = self._clamp(self._blend(s.arousal, arousal_signal))

        # Dominance dimension: friction erodes control, positive roi restores it
        # Friction: high → dominance drops; inverted and scaled
        friction_dom_signal = self._clamp(1.0 - friction * 2.0)  # high friction → -1
        roi_dom_signal       = self._clamp(roi)

        # Blend both signals; roi is a lighter touch
        s.dominance = self._clamp(self._blend(s.dominance, friction_dom_signal))
        s.dominance = self._clamp(self._blend(s.dominance, roi_dom_signal,
                                               alpha_up=0.10, alpha_down=0.02))

        s.tick        += 1
        s.last_update  = time.time()
        self._save()
        return s

    def ingest_ne_state(self, ne_state: dict) -> None:
        """
        Consume NE's internal_state assessment.
        Softer signal than direct interaction data — NE's self-read is a hint.
        Only updates valence and arousal (NE doesn't assess dominance).
        """
        try:
            ne_valence = float(ne_state.get("valence", 0.0))
            ne_arousal = float(ne_state.get("arousal", 0.0))
        except (TypeError, ValueError):
            return

        s = self._state
        s.valence = self._clamp(self._blend(s.valence, ne_valence,
                                             NE_ALPHA_UP, NE_ALPHA_DOWN))
        # NE arousal is [0,1] not [-1,1] — map it
        arousal_signal = self._clamp(ne_arousal * 2.0 - 1.0)
        s.arousal = self._clamp(self._blend(s.arousal, arousal_signal,
                                             NE_ALPHA_UP, NE_ALPHA_DOWN))
        s.last_update = time.time()
        self._save()

    def tick(self) -> MilieuState:
        """
        Natural decay toward neutral. Called by MilieuSource timer even when
        there are no new interactions — mood gradually normalizes with time.
        """
        s = self._state
        s.valence   *= DECAY
        s.arousal   *= DECAY
        s.dominance  = s.dominance * DECAY + (0.3 * (1.0 - DECAY))  # decays toward +0.3, not 0
        s.last_update = time.time()
        self._save()
        return s

    def get_state(self) -> MilieuState:
        """Return current state (read-only view)."""
        return self._state

    def state_csb(self) -> str:
        """Format current state as CSB string for TWM/ring."""
        s = self._state
        return (
            f"MOOD_STATE|v={s.valence:.2f}|a={s.arousal:.2f}|d={s.dominance:.2f}"
            f"|tick={s.tick}"
        )

    def delta(self, prev: MilieuState) -> float:
        """Max absolute change across dims since prev snapshot."""
        s = self._state
        return max(
            abs(s.valence   - prev.valence),
            abs(s.arousal   - prev.arousal),
            abs(s.dominance - prev.dominance),
        )

    def snapshot(self) -> MilieuState:
        """Return a copy of current state for delta comparison."""
        s = self._state
        return MilieuState(
            valence=s.valence,
            arousal=s.arousal,
            dominance=s.dominance,
            tick=s.tick,
            last_update=s.last_update,
        )


# ── Module singleton ───────────────────────────────────────────────────────────

_milieu: Optional[Milieu] = None


def init(instance_id: str) -> Milieu:
    """Initialize the module singleton. Call once at boot."""
    global _milieu
    _milieu = Milieu(instance_id)
    return _milieu


def get() -> Optional[Milieu]:
    """Return the singleton, or None if not yet initialized."""
    return _milieu
