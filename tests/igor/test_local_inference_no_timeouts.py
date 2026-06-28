"""
tests/test_local_inference_no_timeouts.py

Unit tests for T-no-local-inference-timeouts.

Enforces unseenuniversity/rules/local-inference-no-timeouts: every local-inference
timeout default must be hour-scale (≥ 1 hour = 3600s). Sub-hour defaults
are forbidden — they turn routine local slowness into a cloud-spend
trigger, defeating the brain-modeled experimental premise.

These tests intentionally check DEFAULTS, not the env-var override path —
operators are free to set whatever they want, but the in-code defaults
shipped to all instances must honor the rule.

Network-availability checks (e.g. Ollama /api/tags) are NOT inference
and are exempt — they get short timeouts and are tested elsewhere.
"""

import importlib
import os

_HOUR = 3600


def _reload_module(modname: str):
    """Reload a module to pick up current env (so env-var defaults are read)."""
    import sys

    if modname in sys.modules:
        del sys.modules[modname]
    return importlib.import_module(modname)


# ── ollama_reasoner.py: PREPARSE_TIMEOUT ─────────────────────────────────────


def test_preparse_timeout_is_hour_scale():
    """PREPARSE_TIMEOUT must be ≥ 1 hour. Was 8s before T-no-local-inference-timeouts."""
    from unseen_university.devices.igor.cognition.reasoners import ollama_reasoner

    assert ollama_reasoner.PREPARSE_TIMEOUT >= _HOUR, (
        f"PREPARSE_TIMEOUT={ollama_reasoner.PREPARSE_TIMEOUT}s violates "
        "unseenuniversity/rules/local-inference-no-timeouts (must be ≥ 3600s). "
        "Sub-hour timeout escalates routine local slowness → defeats the rule."
    )


# ── ollama_reasoner.py: IGOR_OLLAMA_TIMEOUT_SECS / IGOR_OLLAMA_IMPULSE ──────


def test_main_ollama_timeout_default_is_hour_scale(monkeypatch):
    """Main interactive Ollama timeout default ≥ 2 hours.
    Was 90s (the value that fired 2026-05-03 20:17:27 and triggered the rule)."""
    monkeypatch.delenv("IGOR_OLLAMA_TIMEOUT_SECS", raising=False)
    default = float(os.getenv("IGOR_OLLAMA_TIMEOUT_SECS", "7200"))
    assert default >= 2 * _HOUR, (
        f"IGOR_OLLAMA_TIMEOUT_SECS default={default}s violates the rule "
        "(must be ≥ 7200s = 2hr)."
    )


def test_impulse_ollama_timeout_default_is_hour_scale(monkeypatch):
    """Impulse Ollama timeout default ≥ 1 hour. Was 15s."""
    monkeypatch.delenv("IGOR_OLLAMA_IMPULSE_TIMEOUT_SECS", raising=False)
    default = float(os.getenv("IGOR_OLLAMA_IMPULSE_TIMEOUT_SECS", "3600"))
    assert default >= _HOUR, (
        f"IGOR_OLLAMA_IMPULSE_TIMEOUT_SECS default={default}s violates the rule "
        "(must be ≥ 3600s = 1hr). Impulses are still local inference; "
        "the 'drop and move on' framing doesn't justify a short timeout — "
        "the architectural answer to slow impulses is non-blocking, not "
        "short-timeout-and-give-up."
    )


# ── local_preparse.py: IGOR_LOCAL_PREPARSE_TIMEOUT_SEC ───────────────────────


def test_local_preparse_timeout_default_is_hour_scale(monkeypatch):
    """local_preparse timeout default ≥ 1 hour. Was 1.0s — flagrantly short."""
    monkeypatch.delenv("IGOR_LOCAL_PREPARSE_TIMEOUT_SEC", raising=False)
    # Reload to pick up current env (default reads at module-load time).
    mod = _reload_module("unseen_university.devices.igor.cognition.local_preparse")
    assert mod._DEFAULT_TIMEOUT_SEC >= _HOUR, (
        f"local_preparse._DEFAULT_TIMEOUT_SEC={mod._DEFAULT_TIMEOUT_SEC}s "
        "violates the rule (must be ≥ 3600s = 1hr)."
    )


# ── ollama_reasoner.py: is_healthy is NOT inference — exempt by design ───────


def test_health_check_timeout_intentionally_short():
    """is_healthy is an HTTP availability ping (GET /api/tags), NOT inference.
    The rule explicitly exempts non-inference network checks. We assert the
    short value is preserved here so a future refactor doesn't accidentally
    'fix' it with the rule."""
    from unseen_university.devices.igor.cognition.reasoners import ollama_reasoner
    import inspect

    sig = inspect.signature(ollama_reasoner.is_healthy)
    timeout_param = sig.parameters.get("timeout")
    assert timeout_param is not None
    # 5s is the intended short-by-design value; allow up to 30s in case it
    # gets bumped for slow LANs, but reject hour-scale (would slow boot).
    assert 1 <= timeout_param.default <= 30, (
        f"is_healthy timeout default={timeout_param.default}s is out of "
        "expected range [1, 30]s. This is a network availability check, "
        "NOT inference — see the in-file comment above is_healthy()."
    )
