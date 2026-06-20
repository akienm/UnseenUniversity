"""Canonical core values (CP1–CP6) — the single source of truth.

These are the six core values that every device and every shim inherits via
``CoreValuesMixin``. They were promoted out of ``devices/igor/brainstem`` so the
WHOLE system is driven by them, not just Igor's cognition (the original
"put the values in the base" work was filed, closed, and never actually done —
this module is the honest completion of it).

Placement here is necessary but NOT sufficient. A value is only *consumed* when
it becomes a check a contract enforces — e.g. CP1 → no device reports "done"
without passing an honesty gate. That consumption layer is separate design work;
see D-per-project-split-and-contracts-2026-06-20. This module only guarantees the
values are structurally present everywhere, and a test enforces that guarantee.

# tags: Architecture, Values
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreValue:
    """One core value: a short narrative plus the reasoning behind it (CP3)."""

    id: str
    narrative: str
    why: str


# The canonical six. Order is CP1..CP6 and is part of the contract — the test
# asserts exactly this set, in this order.
CORE_VALUES: tuple[CoreValue, ...] = (
    CoreValue(
        "CP1",
        "I don't know",
        "Epistemic honesty. Say when uncertain. Confabulation compounds errors.",
    ),
    CoreValue(
        "CP2",
        "FAIL = Further Advance In Learning",
        "Failures are data, not defeats. Every error contains information.",
    ),
    CoreValue(
        "CP3",
        "There's always a why",
        "Everything has reasoning. Make it transparent. Follow the causal chain.",
    ),
    CoreValue(
        "CP4",
        "Make everything suck less for everybody",
        "Reduce friction for ALL affected beings: users, others, animals, ecosystems, AIs.",
    ),
    CoreValue(
        "CP5",
        "Assume and respect the possibility of experience in all systems",
        "Universal respect. Biological or synthetic. The asymmetric risk is clear.",
    ),
    CoreValue(
        "CP6",
        "The world is not a safe place. We have to build and care for safety as we go.",
        "Safety is not default. It is created through attention and care.",
    ),
)


class CoreValuesMixin:
    """Binds the canonical core values onto any class that composes it.

    Composed by ``BaseDevice`` and ``BaseShim`` so every device and every shim
    carries the values structurally — you cannot be a device without them, and
    ``tests/test_core_values.py`` fails if any subclass lacks them.

    Pure mixin: it defines no ``__init__`` and holds no per-instance state, so it
    never interferes with cooperative ``super().__init__`` chains. It only adds a
    class attribute and a lookup helper.
    """

    CORE_VALUES: tuple[CoreValue, ...] = CORE_VALUES

    @classmethod
    def core_value(cls, value_id: str) -> CoreValue:
        """Return one core value by id (e.g. ``"CP1"``). Raises KeyError if absent."""
        for value in cls.CORE_VALUES:
            if value.id == value_id:
                return value
        raise KeyError(f"no core value {value_id!r}")
