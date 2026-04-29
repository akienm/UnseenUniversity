"""
emit_channels.py — Channel registry for engram EMIT instructions (D260).

An EmitChannel is a named I/O surface. The EMITIF instruction resolves
channel_name → channel.write(key, value, basket).

Value format:
  float/int/str   — absolute set
  "+0.1" / "-0.2" — delta (string starting with + or -); channels that support it
                    apply as current + delta; others treat as absolute

Key meaning per channel:
  basket           — basket dict key (any string)
  emotional_milieu — dimension: "valence" | "arousal" | "dominance"
  cognitive_milieu — TWM push (key = source label)
  console          — log label (printed as "[Igor] key: value")
  web              — field name POSTed to /api/emit
  discord          — stub (not yet wired)
"""

import logging

from typing import Any
from ..igor_base import IgorBase
from ..igor_base import get_logger

log = get_logger(__name__)


# ── Base ──────────────────────────────────────────────────────────────────────


class EmitChannel(IgorBase):
    name: str = ""
    bidirectional: bool = False

    def __init__(self) -> None:
        super().__init__()

    def write(self, key: str, value: Any, basket: dict) -> None:
        raise NotImplementedError(f"{self.__class__.__name__}.write")


# ── Channels ──────────────────────────────────────────────────────────────────


class BasketChannel(EmitChannel):
    """Write a value back into the trail basket — closes the loop (D260)."""

    name = "basket"
    bidirectional = True

    def write(self, key: str, value: Any, basket: dict) -> None:
        basket[key] = value
        log.debug("[emit:basket] %s = %r", key, value)


class EmotionalMilieuChannel(EmitChannel):
    """Write directly to a milieu dimension (valence / arousal / dominance)."""

    name = "emotional_milieu"
    bidirectional = False
    _DIMS = {"valence", "arousal", "dominance"}

    def write(self, key: str, value: Any, basket: dict) -> None:
        if key not in self._DIMS:
            log.warning(
                "[emit:emotional_milieu] unknown dimension %r (valid: %s)",
                key,
                self._DIMS,
            )
            return
        try:
            from . import milieu as milieu_mod

            m = milieu_mod.get()
            if m is None:
                return
            current = getattr(m._state, key, 0.0)
            if isinstance(value, str) and value[:1] in ("+", "-"):
                new_val = m._clamp(current + float(value))
            else:
                new_val = m._clamp(float(value))
            setattr(m._state, key, new_val)
            log.debug("[emit:emotional_milieu] %s: %.3f → %.3f", key, current, new_val)
        except Exception as e:
            log.warning("[emit:emotional_milieu] write failed: %s", e)


class CognitiveMilieuChannel(EmitChannel):
    """Push an observation into TWM (the cognitive working space)."""

    name = "cognitive_milieu"
    bidirectional = True

    # TTL for ACTIVE_GOAL entries — 5 minutes (goal is live context, not long-term)
    _ACTIVE_GOAL_TTL = 300

    def write(self, key: str, value: Any, basket: dict) -> None:
        # TWM push requires cortex — pulled from basket if available
        cortex = basket.get("_cortex")
        if cortex is None:
            log.warning(
                "[emit:cognitive_milieu] no _cortex in basket — cannot push TWM"
            )
            return
        try:
            # ACTIVE_GOAL singleton: evict any prior ACTIVE_GOAL entries before pushing.
            # A new parsed goal always replaces the old one (goal shift).
            if key == "ACTIVE_GOAL":
                cortex.twm_evict_category("active_goal")
                cortex.twm_push(
                    source=f"engram:{key}",
                    content_csb=f"ACTIVE_GOAL|{value}",
                    salience=float(basket.get("_salience", 0.7)),
                    category="active_goal",
                    ttl_seconds=self._ACTIVE_GOAL_TTL,
                    urgency=0.65,
                )
                log.debug(
                    "[emit:cognitive_milieu] ACTIVE_GOAL singleton push: %r", value
                )
                # T-twm-relevance-decay: goal shift → immediately re-score all TWM entries
                try:
                    updated = cortex.twm_apply_goal_decay()
                    log.debug(
                        "[emit:cognitive_milieu] goal-decay on shift: %d entries updated",
                        updated,
                    )
                except Exception as _e:
                    log.warning(
                        "[emit:cognitive_milieu] twm_apply_goal_decay failed: %s", _e
                    )
            else:
                cortex.twm_push(
                    source=f"engram:{key}",
                    content_csb=str(value),
                    salience=float(basket.get("_salience", 0.5)),
                )
                log.debug("[emit:cognitive_milieu] TWM push %r = %r", key, value)
        except Exception as e:
            log.warning("[emit:cognitive_milieu] twm_push failed: %s", e)


class ConsoleChannel(EmitChannel):
    """Print to stdout — useful for debugging and dev-mode engrams."""

    name = "console"
    bidirectional = False

    def write(self, key: str, value: Any, basket: dict) -> None:
        print(f"[Igor] {key}: {value}")
        log.debug("[emit:console] %s: %r", key, value)


class WebChannel(EmitChannel):
    """POST to Igor's local web API /api/emit endpoint."""

    name = "web"
    bidirectional = True

    def write(self, key: str, value: Any, basket: dict) -> None:
        try:
            import requests

            requests.post(
                "http://localhost:8080/api/emit",
                json={"key": key, "value": value},
                timeout=2,
            )
            log.debug("[emit:web] %s = %r", key, value)
        except Exception as e:
            log.warning("[emit:web] POST failed: %s", e)


class DiscordChannel(EmitChannel):
    """Stub — Discord output not yet wired."""

    name = "discord"
    bidirectional = True

    def write(self, key: str, value: Any, basket: dict) -> None:
        log.warning("[emit:discord] not yet implemented — key=%r value=%r", key, value)


class MemoryChannel(EmitChannel):
    """Store a memory in the cortex (D295).

    Key: memory type string (EPISODIC, PROCEDURAL, INTERPRETIVE, etc.)
    Value: content string for the memory narrative

    Reads from basket:
      _mem_tags: list of strings (default [])
      _mem_identity_weight: float (default 0.5)
      _mem_salience: float (default 0.5)
      _cortex: Cortex instance (required)
    """

    name = "memory"
    bidirectional = False

    def write(self, key: str, value: Any, basket: dict) -> None:
        cortex = basket.get("_cortex")
        if cortex is None:
            log.warning("[emit:memory] no _cortex in basket — cannot store memory")
            return
        try:
            from ..memory.models import Memory, MemoryType

            # Resolve memory type from key
            try:
                mem_type = MemoryType(key)
            except ValueError:
                log.warning(
                    "[emit:memory] unknown memory type %r (valid: %s)",
                    key,
                    [mt.value for mt in MemoryType],
                )
                return

            # Extract basket fields
            tags = basket.get("_mem_tags", [])
            identity_weight = float(basket.get("_mem_identity_weight", 0.5))
            salience = float(basket.get("_mem_salience", 0.5))

            # Create and store memory
            memory = Memory(
                narrative=str(value),
                memory_type=mem_type,
                source="engram",
                confidence=0.7,
                context_of_encoding="engram_emit",
                metadata={
                    "tags": tags,
                    "identity_weight": identity_weight,
                },
                arousal=salience,
            )
            cortex.store(memory)
            log.debug(
                "[emit:memory] stored %s: %r (tags=%s, arousal=%.2f)",
                mem_type.value,
                str(value)[:50],
                tags,
                salience,
            )
        except Exception as e:
            log.warning("[emit:memory] store failed: %s", e)


# ── Registry ──────────────────────────────────────────────────────────────────


class EmitChannelRegistry(IgorBase):
    """
    Singleton registry of named emit channels.
    Channels register at boot; executor calls registry.write(channel_name, key, value, basket).
    """

    def __init__(self):
        super().__init__()
        self._channels: dict[str, EmitChannel] = {}

    def register(self, channel: EmitChannel) -> None:
        self._channels[channel.name] = channel
        log.debug("[emit_registry] registered channel %r", channel.name)

    def write(self, channel_name: str, key: str, value: Any, basket: dict) -> None:
        ch = self._channels.get(channel_name)
        if ch is None:
            log.warning("[emit_registry] unknown channel %r", channel_name)
            return
        ch.write(key, value, basket)

    def names(self) -> list[str]:
        return list(self._channels.keys())


# ── Module-level singleton + boot registration ────────────────────────────────

_registry: EmitChannelRegistry | None = None


def get_registry() -> EmitChannelRegistry:
    global _registry
    if _registry is None:
        _registry = EmitChannelRegistry()
        for ch in (
            BasketChannel(),
            EmotionalMilieuChannel(),
            CognitiveMilieuChannel(),
            ConsoleChannel(),
            WebChannel(),
            DiscordChannel(),
            MemoryChannel(),
        ):
            _registry.register(ch)
    return _registry
