"""
Intent extractor MCP tools — thin wrappers around IntentExtractorDevice.

Tool names: intent_predict, intent_validate, intent_patterns.
Module-level device singleton created lazily on first call.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_device = None


def _get_device():
    global _device
    if _device is None:
        from unseen_university.devices.intent.device import IntentExtractorDevice
        _device = IntentExtractorDevice()
        _device._store.ensure_tables()
        log.info("intent tools: device initialised")
    return _device


def intent_predict(context: str, domain: str) -> dict:
    """Classify the intent of a context string using few-shot learning.

    Retrieves validated (context→outcome) examples from devlab for the domain
    and passes them as few-shot context to a Haiku-class inference call.

    Returns {"prediction_id": str, "intent": str, "confidence": float}.
    Pass prediction_id to intent_validate once the actual outcome is known.
    """
    return _get_device().predict(context=context, domain=domain)


def intent_validate(
    actual_outcome: str,
    prediction_id: str | None = None,
) -> dict:
    """Record the ground-truth outcome for a prior prediction.

    prediction_id=None is the post-hoc path: Librarian and other callers can
    log ground-truth examples without a prior predict() call. match is
    unset in that case. Each validate() call is a training signal — future
    predict() calls in the same domain will incorporate it as few-shot context.

    Returns {"status": "ok"}.
    """
    _get_device().validate(actual_outcome=actual_outcome, prediction_id=prediction_id)
    return {"status": "ok"}


def intent_patterns(domain: str) -> list[dict]:
    """Return aggregated intent patterns for the domain.

    Each entry: {"pattern": str, "validation_count": int, "confidence": float}.
    confidence is the fraction of times the pattern was correctly predicted
    when a matching prediction existed.
    Returns [] when fewer than 2 validated examples exist.
    """
    return _get_device().patterns(domain=domain)
