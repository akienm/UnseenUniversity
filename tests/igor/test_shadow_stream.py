"""T-shadow-stream-reasoning: dual-path reasoning + divergence corpus.

Tests MUST NOT invoke cloud LLMs. The gateway is injected as a mock.
Storage writes are suppressed by passing cortex=None (persistence path
silently no-ops, which is the documented must-not-raise contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)

from devices.igor.cognition.shadow_reasoner import (
    DivergenceRecord,
    ReasonResult,
    ShadowReasoner,
    default_shadow,
    record_turn_divergence,
    set_default_shadow,
)


def setup_function(_fn):
    """Reset module-level singleton + env between tests."""
    set_default_shadow(None)
    os.environ.pop("IGOR_SHADOW_STREAM_ENABLED", None)


def _igor_result(
    text: str = "tree-reasoning output", conf: float = 0.8
) -> ReasonResult:
    return ReasonResult(output=text, confidence=conf, latency_ms=50, source="igor")


def _mock_gateway(reply: str = "tutor-frame: have you considered X?") -> MagicMock:
    gw = MagicMock()
    gw.reason.return_value = (reply, 0.0, True)
    return gw


# ── Env gate ─────────────────────────────────────────────────────────────────


def test_disabled_by_default_fire_and_forget_returns_none():
    """IGOR_SHADOW_STREAM_ENABLED unset → fire_and_forget no-ops."""
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    t = s.fire_and_forget("any query", _igor_result())
    assert t is None


def test_explicitly_disabled_no_ops(monkeypatch):
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "false")
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    assert s.fire_and_forget("any query", _igor_result()) is None


def test_enabled_spawns_thread(monkeypatch):
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    t = s.fire_and_forget("explain the pipeline", _igor_result())
    assert t is not None
    t.join(timeout=2.0)
    assert not t.is_alive()


def test_default_is_false_across_restarts(monkeypatch):
    """Regression: the env gate must default OFF so the module is
    safe to merge without a corpus-collection policy in place."""
    monkeypatch.delenv("IGOR_SHADOW_STREAM_ENABLED", raising=False)
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    assert s.fire_and_forget("q", _igor_result()) is None


# ── Tutor path ───────────────────────────────────────────────────────────────


def test_tutor_no_gateway_returns_error_result():
    """When gateway is None, tutor returns an error result (not crash)."""
    s = ShadowReasoner(cortex=None, gateway=None)
    r = s._run_tutor("anything")
    assert r.source == "tutor"
    assert r.error == "no_gateway"
    assert r.output == ""


def test_tutor_gateway_called_with_tutor_mode(monkeypatch):
    """The tutor path calls gateway.reason exactly once."""
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    gw = _mock_gateway("tutor says: consider Y")
    s = ShadowReasoner(cortex=None, gateway=gw)
    r = s._run_tutor("explain X")
    assert r.source == "tutor"
    assert r.error is None
    assert r.output == "tutor says: consider Y"
    assert gw.reason.call_count == 1


def test_tutor_exception_becomes_error_result():
    """Gateway raising → ReasonResult(error=...) not propagation."""
    gw = MagicMock()
    gw.reason.side_effect = RuntimeError("gateway down")
    s = ShadowReasoner(cortex=None, gateway=gw)
    r = s._run_tutor("anything")
    assert r.source == "tutor"
    assert r.error is not None
    assert "RuntimeError" in r.error


# ── Compare logic ────────────────────────────────────────────────────────────


def test_compare_both_empty_not_diverged():
    igor = ReasonResult(output="", confidence=0.5, latency_ms=1, source="igor")
    tutor = ReasonResult(output="", confidence=0.5, latency_ms=1, source="tutor")
    diverged, reason = ShadowReasoner._compare(igor, tutor)
    assert diverged is False
    assert reason == "both_empty"


def test_compare_one_empty_is_divergence():
    igor = ReasonResult(output="hello", confidence=0.5, latency_ms=1, source="igor")
    tutor = ReasonResult(output="", confidence=0.5, latency_ms=1, source="tutor")
    diverged, reason = ShadowReasoner._compare(igor, tutor)
    assert diverged is True
    assert reason == "one_empty"


def test_compare_identical_not_diverged():
    igor = ReasonResult(
        output="consider X and then Y", confidence=0.5, latency_ms=1, source="igor"
    )
    tutor = ReasonResult(
        output="consider X and then Y", confidence=0.5, latency_ms=1, source="tutor"
    )
    diverged, reason = ShadowReasoner._compare(igor, tutor)
    assert diverged is False
    assert "jaccard=1" in reason


def test_compare_low_overlap_is_divergence():
    igor = ReasonResult(
        output="alpha beta gamma", confidence=0.5, latency_ms=1, source="igor"
    )
    tutor = ReasonResult(
        output="completely different output words",
        confidence=0.5,
        latency_ms=1,
        source="tutor",
    )
    diverged, reason = ShadowReasoner._compare(igor, tutor)
    assert diverged is True
    assert "jaccard=" in reason


def test_compare_igor_error_is_divergence():
    igor = ReasonResult(
        output="", confidence=0.0, latency_ms=1, source="igor", error="crash"
    )
    tutor = ReasonResult(output="ok", confidence=0.5, latency_ms=1, source="tutor")
    diverged, reason = ShadowReasoner._compare(igor, tutor)
    assert diverged is True
    assert "igor_error" in reason


# ── fire_and_forget end-to-end ───────────────────────────────────────────────


def test_fire_and_forget_calls_gateway(monkeypatch):
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    gw = _mock_gateway("tutor response")
    s = ShadowReasoner(cortex=None, gateway=gw)
    t = s.fire_and_forget("sample query", _igor_result("igor response"))
    assert t is not None
    t.join(timeout=2.0)
    assert gw.reason.called


def test_fire_and_forget_survives_tutor_exception(monkeypatch):
    """Daemon thread must not propagate; Igor's reply path is untouched."""
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    gw = MagicMock()
    gw.reason.side_effect = RuntimeError("boom")
    s = ShadowReasoner(cortex=None, gateway=gw)
    t = s.fire_and_forget("q", _igor_result())
    t.join(timeout=2.0)
    assert not t.is_alive()


def test_fire_and_forget_persist_skipped_without_cortex(monkeypatch):
    """cortex=None → persist silently no-ops. No exception escapes the thread."""
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    t = s.fire_and_forget("q", _igor_result())
    t.join(timeout=2.0)
    assert not t.is_alive()


# ── Module-level convenience + singleton ─────────────────────────────────────


def test_default_shadow_accessors():
    assert default_shadow() is None
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    set_default_shadow(s)
    assert default_shadow() is s
    set_default_shadow(None)
    assert default_shadow() is None


def test_record_turn_divergence_noop_when_unset(monkeypatch):
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    assert record_turn_divergence("q", _igor_result()) is None


def test_record_turn_divergence_routes_to_singleton(monkeypatch):
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    s = ShadowReasoner(cortex=None, gateway=_mock_gateway())
    set_default_shadow(s)
    t = record_turn_divergence("q", _igor_result())
    assert t is not None
    t.join(timeout=2.0)


def test_record_turn_divergence_swallows_singleton_exceptions(monkeypatch):
    """If the registered shadow raises synchronously from fire_and_forget,
    the convenience must still return None rather than propagate."""
    monkeypatch.setenv("IGOR_SHADOW_STREAM_ENABLED", "true")
    bad = MagicMock()
    bad.fire_and_forget.side_effect = RuntimeError("broken shadow")
    set_default_shadow(bad)
    assert record_turn_divergence("q", _igor_result()) is None


# ── run_shadow_sync (scaffolded for future active mode) ──────────────────────


def test_run_shadow_sync_returns_first_confident():
    """High-confidence igor result returned before tutor completes."""

    def fast_igor():
        return _igor_result("igor won", conf=0.9)

    gw = _mock_gateway("tutor (slower)")

    def slow_reason(*a, **kw):
        import time as _t

        _t.sleep(0.3)
        return ("tutor (slower)", 0.0, True)

    gw.reason.side_effect = slow_reason
    s = ShadowReasoner(cortex=None, gateway=gw, tutor_timeout_sec=2.0)
    result = s.run_shadow_sync("q", fast_igor, confidence_threshold=0.7)
    assert result.source == "igor"
    assert result.output == "igor won"


def test_run_shadow_sync_falls_back_to_first_completed_below_threshold():
    """When neither path clears threshold, return the first completed."""

    def low_conf_igor():
        return _igor_result("igor low conf", conf=0.1)

    gw = _mock_gateway("tutor low conf")
    s = ShadowReasoner(cortex=None, gateway=gw, tutor_timeout_sec=2.0)
    result = s.run_shadow_sync("q", low_conf_igor, confidence_threshold=0.9)
    # Either path can be first; just check we got a non-error result
    assert result.source in ("igor", "tutor")
    assert result.error is None


def test_run_shadow_sync_raises_when_both_error():
    def igor_err():
        return ReasonResult(
            output="", confidence=0.0, latency_ms=1, source="igor", error="x"
        )

    gw = MagicMock()
    gw.reason.side_effect = RuntimeError("gateway err")
    s = ShadowReasoner(cortex=None, gateway=gw, tutor_timeout_sec=2.0)
    import pytest

    with pytest.raises(RuntimeError):
        s.run_shadow_sync("q", igor_err)


# ── Dataclass shape sanity ───────────────────────────────────────────────────


def test_divergence_record_is_dataclass():
    rec = DivergenceRecord(
        session_id="sess",
        turn_id="turn",
        input_csb="input",
        igor=_igor_result(),
        tutor=ReasonResult(output="t", confidence=0.5, latency_ms=10, source="tutor"),
        winner="log_only",
        diverged=False,
        divergence_reason="jaccard=0.5",
    )
    assert rec.session_id == "sess"
    assert rec.igor.source == "igor"
    assert rec.tutor.source == "tutor"
