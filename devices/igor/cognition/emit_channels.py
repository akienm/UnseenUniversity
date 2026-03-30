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

log = logging.getLogger(__name__)


# ── Base ──────────────────────────────────────────────────────────────────────


class EmitChannel:
    name: str = ""
    bidirectional: bool = False

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

    def write(self, key: str, value: Any, basket: dict) -> None:
        # TWM push requires cortex — pulled from basket if available
        cortex = basket.get("_cortex")
        if cortex is None:
            log.warning(
                "[emit:cognitive_milieu] no _cortex in basket — cannot push TWM"
            )
            return
        try:
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


# ── Registry ──────────────────────────────────────────────────────────────────


class EmitChannelRegistry:
    """
    Singleton registry of named emit channels.
    Channels register at boot; executor calls registry.write(channel_name, key, value, basket).
    """

    def __init__(self):
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
        ):
            _registry.register(ch)
    return _registry
