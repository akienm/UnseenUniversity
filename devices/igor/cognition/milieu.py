"""milieu.py — Ambient emotional state manager (3D affect vector).

WHAT IT IS
──────────
Milieu is Igor's slow-drifting 3-dimensional affect vector (valence,
arousal, dominance) that persists across sessions and shapes habit
sensitivity, NE output framing, and background motivation. It is NOT
per-interaction state — it is the ambient emotional weather that
individual interactions push against.

State dimensions (each [-1, 1]):
  valence    pleasant/satisfied ↔ unpleasant/frustrated
  arousal    activated/energized ↔ deactivated/calm/tired
  dominance  in-control ↔ overwhelmed (baseline +0.3 = default competent)

WHY IT EXISTS
─────────────
Igor's cognition is affectively grounded. Milieu sets the background
tempo: high arousal suppresses unurgent background salience (focus on
critical path); low arousal boosts exploratory surfacing (idle time =
learning time). Dominance erodes on escalation surprises (had to call a
tier higher than predicted) and restores on successful local resolution —
closing the prediction loop. Boredom fires when arousal stays flat and
low; curiosity fires on positive valence + low arousal. This substrate
is alive — mood affects what Igor notices, not via explicit if-statements
but via salience modulation and threshold nudging.

HOW IT WORKS (architecture)
───────────────────────────

1. State model
   MilieuState dataclass (valence, arousal, dominance, tick, last_update).
   Persisted to JSON so mood survives restarts. Ring-buffered history
   (50 timestamped VAD rows) tracks trajectory. Gradient detection:
   arousal slope over N rows → climbing alert (loop detection).

2. Update mechanisms (four signal sources)
   a) update(valence, friction, roi) — direct interaction outcome
      • valence: from PFC assessment
      • friction [0,1]: stress/resource pressure; high → high arousal +
        low dominance
      • roi [-1,1]: return-on-investment; positive → dominance restoration
   b) ingest_ne_state(ne_state) — NE self-narrative (softer signal)
      • Updates valence + arousal only.
   c) ingest_resolution_reward(valence) — per-turn reward signal
      • Updates valence only.
   d) ingest_surprise(predicted_tier, actual_tier) — escalation gap
      • Escalation surprise (actual > predicted) → dominance erodes +
        arousal spikes.
      • Local success (actual ≤ predicted) → mild dominance restoration.
      • Closes the prediction loop: repeated escalations erode confidence.

3. Diffusion & decay
   Asymmetric EMA (fast rise α=0.25, slow fall α=0.05) gives mood
   stickiness: positive experiences are memorable; negative ones fade
   gradually. Per-dim decay toward setpoint:
     valence   0.96 × tick + 0.1 setpoint  (volatile, resets quickly)
     arousal   0.97 × tick + 0.05 setpoint (activation persists longer)
     dominance 0.99 × tick + 0.3 setpoint  (control is most stable)
   tick() called by MilieuSource timer (60s) normalizes mood even during
   idle periods. Every GLOBAL_SYNC_TICKS (10), gently blend toward global
   baseline so long sessions don't drift from cluster baseline.

4. Propagation to other subsystems
   • TWM: MilieuSource pushes MOOD_STATE CSB entry (low salience) every 60s.
   • NE: reads MOOD_STATE; frames response tone by arousal/valence.
   • BG (basal ganglia): habit scoring weighted by arousal. Low arousal →
     lower threshold (exploratory); high arousal → raised threshold
     (focus). Per D037: threshold modulated [0.30, 0.70] by arousal.
   • Salience modulation: high arousal suppresses low-urgency background
     salience; low arousal boosts exploratory impulses (D308).
   • Emit channels: emotional_milieu channel allows engrams to nudge
     V/A/D directly (D351).
     e.g., EMITIF arousal>0.5 THEN emotional_milieu.arousal="-0.1".

5. Boredom threshold integration
   BoredomSource watches arousal: if arousal < AROUSAL_THRESH for
   WINDOW_MINS, fires BOREDOM_DETECTED → ACTION_IMPULSE → foreman_scan.
   Also nudges milieu with slight negative valence so stillness becomes
   slightly uncomfortable, priming motion. Cooldown prevents cascade
   anxiety (D272).

6. History & trajectory
   Ring buffer tracks timestamped (valence, arousal, dominance) + slope
   detection. gradient(dim, n) computes (last - first) / n over history
   window. is_arousal_climbing() fires when gradient exceeds
   AROUSAL_SLOPE_ALERT (0.03). Sustained escalation triggers parent habit
   to decay both loop ends + inject regulate observation.

7. Global synchronization (D093 — remote-instance architecture basis)
   Cross-instance milieu: each Igor instance blends toward global
   baseline every GLOBAL_SYNC_TICKS ticks (gentle 2% per tick). On spikes
   (Δ ≥ 0.15 on any dim), contribute immediately with
   GLOBAL_ALPHA_SPIKE; on routine ticks, GLOBAL_ALPHA_ROUTINE (slower).
   If IGOR_GLOBAL_MILIEU_URL is set, also push contributions + read
   global from remote HTTP endpoint. Coordination is advisory — never
   blocks, never raises.

8. Gap reset (post-sleep)
   After > 4h idle (The Gap), emotional state from before sleep is
   stale. gap_reset() aggressively decays:
     arousal  × 0.3 (activation transient)
     valence  × 0.5 (moderately)
     dominance × 0.7 + 0.3 × 0.3 toward competent baseline.

9. Session histogram
   session_histogram() computes per-dim distribution (min/max/mean/std/
   bins) + session character (bouncy | stressed | focused | calm). Bins
   are 5 equal buckets [-1, 1]. Character reflects problem-solving
   pattern (bouncy), resource pressure (stressed), flow state (focused),
   or rest (calm).

ENGRAM PORTION (graph-resident machinery)
─────────────────────────────────────────
  PROC_BOREDOM_TRIGGER      — habit fires BOREDOM_DETECTED when arousal
                               flats
  InteroceptionSource       — 30s continuous VAD gradient from CPU/mem/
                               disk → nudge_vad()
  EmotionalMilieuChannel    — EMITIF instruction channel for engram-
                               driven nudges
  BoredomSource             — pushes BOREDOM_DETECTED to TWM as
                               low-salience background
  MilieuInterruptor         — fires alert when arousal > 0.7 or
                               valence < -0.5 sustained

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D036  milieu                       — 3D affect + asymmetric EMA
  D037  basal-ganglia modulation     — habit threshold [0.30, 0.70]
                                        shaped by arousal
  D082  curiosity idle state         — low arousal + positive valence
  D093  remote-instance architecture — basis for cross-instance milieu
                                        sync
  D101  milieu ring buffer + gradient detection
  D133  session-in-db                — milieu state persisted to Postgres
  D184  affective NE frame selection — arousal weights promotion
                                        importance
  D185  affective NE gap closure     — tension drives reward valence
  D186  affective NE arousal amplification — gap salience × arousal
  D243  NE deterministic arc         — boredom impulse no LLM gate
  D246  emit+react cognitive milieu  — substrate frame
  D252  Calibre 8-tier arousal       — reading encoding_arousal
  D272  boredom idle loop            — arousal flat + low →
                                        BOREDOM_DETECTED
  D308  milieu-aware salience        — high arousal suppresses
                                        background
  D351  channel-emit model           — emotional_milieu channel for
                                        engrams

Consumers & integration points
──────────────────────────────
  NarrativeEngine          — reads MOOD_STATE; frames tone
  BasePushSource.milieu_scale() — salience/urgency modulated by arousal
  Basal ganglia            — habit threshold shaped by arousal
  BoredomSource            — watches arousal; fires BOREDOM_DETECTED
  MilieuInterruptor        — fires on extreme arousal or sustained
                              negative valence
  InteroceptionSource      — 30s VAD gradient from system resources
  EmotionalMilieuChannel   — engrams nudge V/A/D via EMITIF
  Reading list             — encoding_arousal tier affects priority

Public API (module singleton via init() / get())
────────────────────────────────────────────────
  init(instance_id), get()
  update(valence, friction, roi)
  ingest_ne_state(ne_state), ingest_resolution_reward(valence),
    ingest_surprise(predicted_tier, actual_tier)
  nudge_vad(dv, da, dd)  — direct signed deltas (interoception)
  tick()  — natural decay
  get_state(), snapshot(), delta(prev)
  gap_reset()  — post-sleep decay
  gradient(dim, n), is_arousal_climbing(threshold, n)
  session_histogram(), state_csb()
"""

from __future__ import annotations
import logging

import json
import sys
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from ..igor_base import IgorBase
from ..paths import paths

# ── Cross-platform file locking ────────────────────────────────────────────────
if sys.platform == "win32":
    import msvcrt

    def _flock_ex(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _flock_un(f) -> None:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )

else:
    import fcntl

    def _flock_ex(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX)

    def _flock_un(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


# ── Constants ──────────────────────────────────────────────────────────────────

ALPHA_UP = 0.25  # fast rise toward new signal
ALPHA_DOWN = 0.05  # slow fall away from signal
PUSH_DELTA = 0.08  # min per-dim change required to push a TWM update

# D101: milieu time series — ring buffer of V/A/D rows + gradient detection
HISTORY_MAX = 50  # max rows kept (ring; oldest evicted)
HISTORY_MIN_DELTA = 0.02  # min per-dim change to record a new history row
AROUSAL_SLOPE_ALERT = 0.03  # arousal rising by this much per row = climbing alert
AROUSAL_SLOPE_N = 5  # look back N rows for slope computation

# G12 / #55: per-dimension asymmetric decay rates (faster for volatile dims)
DECAY_VALENCE = 0.96  # fastest — mood is volatile, fades quickly
DECAY_AROUSAL = 0.97  # medium — activation persists somewhat longer
DECAY_DOMINANCE = 0.99  # slowest — sense of control is most stable
HOMEOSTATIC_VALENCE_SETPOINT = float(
    os.getenv("IGOR_HOMEOSTATIC_VALENCE_SETPOINT", "0.1")
)
HOMEOSTATIC_AROUSAL_SETPOINT = float(
    os.getenv("IGOR_HOMEOSTATIC_AROUSAL_SETPOINT", "0.05")
)

# NE's self-assessment is a softer hint than direct interaction signals
NE_ALPHA_UP = 0.10
NE_ALPHA_DOWN = 0.03

# Per-turn resolution reward: faster than NE hint, slower than direct interaction
RESOLUTION_ALPHA_UP = 0.15
RESOLUTION_ALPHA_DOWN = 0.04

# Global milieu: shared across all instances with same ~/.TheIgors root
SPIKE_THRESHOLD = 0.15  # min delta on any dim to classify as spike
GLOBAL_ALPHA_SPIKE = 0.05  # global EMA speed on notable change
GLOBAL_ALPHA_ROUTINE = 0.01  # global EMA speed on routine tick

# G16 / #56: cross-instance sync
# Local: tick() blends toward global every GLOBAL_SYNC_TICKS ticks
# Cross-machine: POST contributions + GET global from IGOR_GLOBAL_MILIEU_URL
GLOBAL_BLEND_ALPHA = (
    0.02  # 2% pull toward global per sync tick — gentle, non-overriding
)
GLOBAL_SYNC_TICKS = 10  # blend every N ticks (~5 min at 30s/tick)


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

    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.3  # start slight positive (default competent)
    tick: int = 0
    last_update: float = 0.0


# ── Global milieu helpers ──────────────────────────────────────────────────────


def _global_milieu_path() -> Path:
    return paths().milieu


def _contribute_to_global(state: MilieuState, alpha: float) -> None:
    """
    Slow-EMA contribution from one instance's current state to the shared global.
    Uses a lock file for safe concurrent writes from multiple instances.
    Never raises — global milieu is advisory.
    """
    path = _global_milieu_path()
    lock_path = path.with_suffix(".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lf:
            _flock_ex(lf)
            try:
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        g = MilieuState(
                            **{
                                k: v
                                for k, v in data.items()
                                if k in MilieuState.__dataclass_fields__
                            }
                        )
                    except Exception:
                        g = MilieuState()
                else:
                    g = MilieuState()
                g.valence += alpha * (state.valence - g.valence)
                g.arousal += alpha * (state.arousal - g.arousal)
                g.dominance += alpha * (state.dominance - g.dominance)
                g.last_update = time.time()
                path.write_text(json.dumps(asdict(g), indent=2), encoding="utf-8")
            finally:
                _flock_un(lf)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
        )


# ── Core Milieu class ──────────────────────────────────────────────────────────


class Milieu(IgorBase):
    """
    Ambient emotional state manager.
    One instance per Igor process (module singleton via init()/get()).
    """

    def __init__(self, instance_id: str):
        super().__init__()
        self._instance_id = instance_id
        self._path = paths().instance / "milieu.json"
        # D101: persisted history ring
        self._history_path = self._path.parent / "milieu_history.json"
        _local_existed = self._path.exists()
        self._state = self._load()
        # D101: load history ring from disk
        self._history: list[dict] = self._load_history()
        # New instance with no local history: seed from global baseline
        if not _local_existed:
            g = self._load_global_baseline()
            self._state.valence = g.valence
            self._state.arousal = g.arousal
            self._state.dominance = g.dominance
        # #99: session histogram — in-memory only, never persisted
        self._session_samples: list[tuple[float, float, float]] = []
        # G16: tick counter for global sync cadence
        self._tick_count: int = 0

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> MilieuState:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return MilieuState(
                    **{
                        k: v
                        for k, v in data.items()
                        if k in MilieuState.__dataclass_fields__
                    }
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )
        return MilieuState()

    def _read_global(self) -> "MilieuState | None":
        """
        G16: Read global milieu — local file first, then remote URL if configured.

        IGOR_GLOBAL_MILIEU_URL: HTTP endpoint of the main_loop instance serving
        GET /api/milieu/global. If set and reachable, remote overrides local file.
        Falls back to local file silently. Returns None if nothing available.
        """
        _remote_url = os.getenv("IGOR_GLOBAL_MILIEU_URL", "").strip()
        if _remote_url:
            try:
                import urllib.request as _req
                import json as _j

                with _req.urlopen(
                    f"{_remote_url.rstrip('/')}/api/milieu/global", timeout=3
                ) as resp:
                    data = _j.loads(resp.read().decode())
                return MilieuState(
                    **{
                        k: v
                        for k, v in data.items()
                        if k in MilieuState.__dataclass_fields__
                    }
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
                )
        return self._load_global_baseline()

    def _push_to_remote(self) -> None:
        """
        G16: Push this instance's contribution to the remote global milieu endpoint.
        Only called when IGOR_GLOBAL_MILIEU_URL is set (cross-machine scenario).
        Fire-and-forget — never blocks or raises.
        """
        _remote_url = os.getenv("IGOR_GLOBAL_MILIEU_URL", "").strip()
        if not _remote_url:
            return
        try:
            import urllib.request as _req
            import json as _j

            _payload = _j.dumps(asdict(self._state)).encode()
            _req_obj = _req.Request(
                f"{_remote_url.rstrip('/')}/api/milieu/contribute",
                data=_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _req.urlopen(_req_obj, timeout=3)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )

    def _load_global_baseline(self) -> MilieuState:
        try:
            path = _global_milieu_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return MilieuState(
                    **{
                        k: v
                        for k, v in data.items()
                        if k in MilieuState.__dataclass_fields__
                    }
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )
        return MilieuState()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(asdict(self._state), indent=2),
                encoding="utf-8",
            )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )
        self._append_history()

    # ── D101: History ring buffer ──────────────────────────────────────────────

    def _load_history(self) -> list[dict]:
        """D101: Load persisted history ring from disk."""
        try:
            if self._history_path.exists():
                data = json.loads(self._history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-HISTORY_MAX:]
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )
        return []

    def _save_history(self) -> None:
        try:
            self._history_path.write_text(
                json.dumps(self._history[-HISTORY_MAX:]),
                encoding="utf-8",
            )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/milieu.py: %s", _bare_e
            )

    def _append_history(self) -> None:
        """
        D101: Append current state to history ring if it has changed enough.
        Reactivation creates a new row at NOW (not revival of old row).
        """
        s = self._state
        if self._history:
            last = self._history[-1]
            delta = max(
                abs(s.valence - last["v"]),
                abs(s.arousal - last["a"]),
                abs(s.dominance - last["d"]),
            )
            if delta < HISTORY_MIN_DELTA:
                return
        self._history.append(
            {
                "t": time.time(),
                "v": round(s.valence, 4),
                "a": round(s.arousal, 4),
                "d": round(s.dominance, 4),
            }
        )
        if len(self._history) > HISTORY_MAX:
            self._history = self._history[-HISTORY_MAX:]
        self._save_history()

    def gradient(self, dim: str = "arousal", n: int = AROUSAL_SLOPE_N) -> float:
        """
        D101: Compute slope of `dim` over the last `n` history rows.
        Returns (last - first) / n — positive means rising, negative means falling.
        Returns 0.0 if insufficient history.
        """
        h = self._history
        if len(h) < 2:
            return 0.0
        window = h[-min(n, len(h)) :]
        if len(window) < 2:
            return 0.0
        first = window[0].get(dim[0], 0.0)  # "arousal" → "a", "valence" → "v", etc.
        last = window[-1].get(dim[0], 0.0)
        return (last - first) / len(window)

    def is_arousal_climbing(
        self,
        threshold: float = AROUSAL_SLOPE_ALERT,
        n: int = AROUSAL_SLOPE_N,
    ) -> bool:
        """D101: True if arousal has been rising steadily over the last n history rows."""
        return self.gradient("arousal", n) >= threshold

    # ── Math ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _blend(
        current: float,
        signal: float,
        alpha_up: float = ALPHA_UP,
        alpha_down: float = ALPHA_DOWN,
    ) -> float:
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
        _prev = self.snapshot()
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
        roi_dom_signal = self._clamp(roi)

        # Blend both signals; roi is a lighter touch
        s.dominance = self._clamp(self._blend(s.dominance, friction_dom_signal))
        s.dominance = self._clamp(
            self._blend(s.dominance, roi_dom_signal, alpha_up=0.10, alpha_down=0.02)
        )

        s.tick += 1
        s.last_update = time.time()
        self._save()
        # #99: accumulate session histogram sample
        self._session_samples.append((s.valence, s.arousal, s.dominance))
        _alpha = (
            GLOBAL_ALPHA_SPIKE
            if self.delta(_prev) >= SPIKE_THRESHOLD
            else GLOBAL_ALPHA_ROUTINE
        )
        _contribute_to_global(s, _alpha)
        # G16: on spike, push to remote immediately (cross-machine propagation)
        if _alpha == GLOBAL_ALPHA_SPIKE:
            self._push_to_remote()
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

        _prev = self.snapshot()
        s = self._state
        s.valence = self._clamp(
            self._blend(s.valence, ne_valence, NE_ALPHA_UP, NE_ALPHA_DOWN)
        )
        # NE arousal is [0,1] not [-1,1] — map it
        arousal_signal = self._clamp(ne_arousal * 2.0 - 1.0)
        s.arousal = self._clamp(
            self._blend(s.arousal, arousal_signal, NE_ALPHA_UP, NE_ALPHA_DOWN)
        )
        s.last_update = time.time()
        self._save()
        if self.delta(_prev) >= SPIKE_THRESHOLD:
            _contribute_to_global(s, GLOBAL_ALPHA_SPIKE)

    def ingest_resolution_reward(self, valence: float) -> None:
        """
        Per-turn reward signal: called when a turn resolves with a response.

        Stronger than NE hint (α=0.15/0.04) but weaker than direct interaction.
        Only updates valence — turn resolution doesn't affect arousal or dominance.
        Contributes to global milieu on spike.
        """
        try:
            valence = float(valence)
        except (TypeError, ValueError):
            return

        _prev = self.snapshot()
        s = self._state
        s.valence = self._clamp(
            self._blend(s.valence, valence, RESOLUTION_ALPHA_UP, RESOLUTION_ALPHA_DOWN)
        )
        s.last_update = time.time()
        self._save()
        if self.delta(_prev) >= SPIKE_THRESHOLD:
            _contribute_to_global(s, GLOBAL_ALPHA_SPIKE)

    def tick(self) -> MilieuState:
        """
        Natural decay toward neutral. Called by MilieuSource timer even when
        there are no new interactions — mood gradually normalizes with time.

        G12 / #55: per-dimension rates — valence fastest (volatile), dominance slowest (stable).
        G16 / #56: every GLOBAL_SYNC_TICKS ticks, blend gently toward global baseline.
        """
        s = self._state
        s.valence = s.valence * DECAY_VALENCE + (
            HOMEOSTATIC_VALENCE_SETPOINT * (1.0 - DECAY_VALENCE)
        )
        s.arousal = s.arousal * DECAY_AROUSAL + (
            HOMEOSTATIC_AROUSAL_SETPOINT * (1.0 - DECAY_AROUSAL)
        )
        s.dominance = s.dominance * DECAY_DOMINANCE + (0.3 * (1.0 - DECAY_DOMINANCE))

        # G16: periodic pull toward global — keeps long sessions from drifting too far
        self._tick_count += 1
        if self._tick_count % GLOBAL_SYNC_TICKS == 0:
            g = self._read_global()
            if g is not None:
                s.valence += GLOBAL_BLEND_ALPHA * (g.valence - s.valence)
                s.arousal += GLOBAL_BLEND_ALPHA * (g.arousal - s.arousal)
                s.dominance += GLOBAL_BLEND_ALPHA * (g.dominance - s.dominance)

        s.last_update = time.time()
        self._save()
        _contribute_to_global(s, GLOBAL_ALPHA_ROUTINE)
        return s

    def ingest_surprise(self, predicted_tier: str, actual_tier: str) -> None:
        """
        Dopamine-analog prediction signal (G5 / #42).

        Compare predicted tier (minimum Igor expected to need) vs actual tier used.
        Exceeding prediction (had to escalate further than expected) → dominance hit + arousal spike.
        Meeting or beating prediction → mild dominance restoration.

        This closes the prediction loop: repeated escalation-surprises erode dominance
        (Igor loses confidence); consistent local resolution gradually rebuilds it.
        """
        _prev = self.snapshot()
        _TIER_ORDER: dict[str, float] = {
            "tier.1": 1.0,
            "tier.2": 2.0,
            "tier.3": 3.0,
            "tier.3.5": 3.5,
            "tier.4": 4.0,
            "tier.5": 5.0,
            "tier.6": 6.0,
        }
        pred_n = _TIER_ORDER.get(predicted_tier, 3.5)
        actual_n = _TIER_ORDER.get(actual_tier, 3.5)

        s = self._state
        if actual_n > pred_n:
            # Had to escalate further — prediction failed → dominance erodes, arousal spikes
            magnitude = min(0.5, (actual_n - pred_n) * 0.25)
            dom_signal = self._clamp(s.dominance - magnitude)
            s.dominance = self._clamp(
                self._blend(s.dominance, dom_signal, alpha_up=0.20, alpha_down=0.05)
            )
            aro_signal = self._clamp(s.arousal + 0.15)
            s.arousal = self._clamp(
                self._blend(s.arousal, aro_signal, alpha_up=0.20, alpha_down=0.05)
            )
        else:
            # Succeeded at or below predicted tier — mild confidence restoration
            dom_signal = self._clamp(s.dominance + 0.08)
            s.dominance = self._clamp(
                self._blend(s.dominance, dom_signal, alpha_up=0.10, alpha_down=0.02)
            )

        s.last_update = time.time()
        self._save()
        if self.delta(_prev) >= SPIKE_THRESHOLD:
            _contribute_to_global(s, GLOBAL_ALPHA_SPIKE)

    def nudge_vad(self, dv: float, da: float, dd: float) -> MilieuState:
        """
        T-interoception: Apply signed VAD deltas directly to current state.

        Used by InteroceptionSource to apply resource-derived gradients without
        the friction→arousal remapping that update() performs. Deltas are additive
        (not EMA blend toward a target) so small repeated nudges accumulate naturally.

        dv, da, dd each in [-1, 1]; clamped after addition to keep state in range.
        Contributes to global milieu on any change (routine alpha).
        """
        _prev = self.snapshot()
        s = self._state
        s.valence = self._clamp(s.valence + dv)
        s.arousal = self._clamp(s.arousal + da)
        s.dominance = self._clamp(s.dominance + dd)
        s.last_update = time.time()
        self._save()
        self._session_samples.append((s.valence, s.arousal, s.dominance))
        _alpha = (
            GLOBAL_ALPHA_SPIKE
            if self.delta(_prev) >= SPIKE_THRESHOLD
            else GLOBAL_ALPHA_ROUTINE
        )
        _contribute_to_global(s, _alpha)
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
            abs(s.valence - prev.valence),
            abs(s.arousal - prev.arousal),
            abs(s.dominance - prev.dominance),
        )

    def session_histogram(self) -> dict:
        """
        #99: Compute per-dimension distribution stats for this session.

        Returns a dict with per-dim (min/max/mean/std/bins) and a session_character
        classification: bouncy | stressed | focused | calm | insufficient_data.

        Bins are 5 equal buckets across [-1, 1]: very_neg, neg, neutral, pos, very_pos.
        """
        samples = self._session_samples
        if len(samples) < 3:
            return {
                "session_character": "insufficient_data",
                "sample_count": len(samples),
            }

        def _stats(vals: list[float]) -> dict:
            n = len(vals)
            mean = sum(vals) / n
            std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
            buckets = [0, 0, 0, 0, 0]  # very_neg, neg, neutral, pos, very_pos
            for v in vals:
                idx = min(4, int((v + 1.0) / 0.4))
                buckets[idx] += 1
            return {
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "mean": round(mean, 3),
                "std": round(std, 3),
                "bins": buckets,  # [very_neg, neg, neutral, pos, very_pos]
            }

        v_vals = [s[0] for s in samples]
        a_vals = [s[1] for s in samples]
        d_vals = [s[2] for s in samples]
        v_stats = _stats(v_vals)
        a_stats = _stats(a_vals)
        d_stats = _stats(d_vals)

        # Session character classification
        # bouncy   = high std in valence or arousal (oscillating — problem-solving pattern)
        # stressed = low mean valence + high mean arousal
        # focused  = low std, moderate-to-high arousal, positive dominance
        # calm     = low arousal, near-neutral valence
        if v_stats["std"] > 0.25 or a_stats["std"] > 0.25:
            character = "bouncy"
        elif v_stats["mean"] < -0.2 and a_stats["mean"] > 0.2:
            character = "stressed"
        elif a_stats["std"] < 0.15 and a_stats["mean"] > 0.0 and d_stats["mean"] > 0.2:
            character = "focused"
        elif abs(a_stats["mean"]) < 0.15 and abs(v_stats["mean"]) < 0.15:
            character = "calm"
        else:
            character = "neutral"

        return {
            "session_character": character,
            "sample_count": len(samples),
            "valence": v_stats,
            "arousal": a_stats,
            "dominance": d_stats,
        }

    def gap_reset(self) -> MilieuState:
        """
        #134: Partial milieu reset after The Gap (post-sleep boot).
        Emotional state from >4h ago is stale — decay aggressively toward baseline.
        Arousal drops most (activation is transient); valence moderately; dominance
        drifts toward default 0.3 (slightly competent).
        """
        s = self._state
        s.arousal *= 0.3
        s.valence *= 0.5
        s.dominance = s.dominance * 0.7 + 0.3 * 0.3
        s.last_update = time.time()
        self._save()
        return s

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
