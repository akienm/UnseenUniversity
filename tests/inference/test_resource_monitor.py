"""
Tests for T-router-live-resource-read (increment 4 of D-inference-cost-optimizing-router):
LIVE re-measurement so a source's time_bucket reflects its CURRENT speed. A box that got
faster (Hex) is promoted; one that degraded is demoted — buckets are live, not baked.

This is the epic's headline principle. Difficulty has a corrector (failure-bump); a stale
time_bucket had none — this builds that feedback path.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.resource_monitor import (
    ResourceMonitor,
    bucket_for_latency,
)
from unseen_university.devices.inference.rules_engine import RoutingRule, RulesEngine
from unseen_university.devices.inference.sources import Source, SourceRegistry


def _fake_source(name, time_bucket):
    return SimpleNamespace(name=name, time_bucket=time_bucket, cost_class="owned_local", available=True)


# ── pure threshold mapping ────────────────────────────────────────────────────


def test_bucket_for_latency_thresholds():
    assert bucket_for_latency(2.0) == "interactive"
    assert bucket_for_latency(60.0) == "minutes"
    assert bucket_for_latency(600.0) == "overnight"


# ── promote / demote from observed latency ────────────────────────────────────


def test_fast_latencies_promote_to_interactive():
    """The Hex scenario: a box that runs a job in seconds is promoted to interactive."""
    src = _fake_source("hex", "overnight")
    mon = ResourceMonitor()
    for _ in range(3):
        mon.record(src, 2.0)
    assert src.time_bucket == "interactive"


def test_slow_latencies_demote_to_overnight():
    src = _fake_source("degraded", "interactive")
    mon = ResourceMonitor()
    for _ in range(3):
        mon.record(src, 600.0)
    assert src.time_bucket == "overnight"


# ── hysteresis: one outlier must not thrash the bucket ────────────────────────


def test_single_outlier_does_not_flip():
    src = _fake_source("steady", "interactive")
    mon = ResourceMonitor()
    for _ in range(4):
        mon.record(src, 2.0)
    mon.record(src, 999.0)   # one spike
    assert src.time_bucket == "interactive", "a single outlier must not demote the bucket"


def test_below_min_samples_no_premature_flip():
    """One sample is not enough to move the bucket — the window needs to fill."""
    src = _fake_source("cold", "overnight")
    mon = ResourceMonitor()
    mon.record(src, 2.0)
    assert src.time_bucket == "overnight"


# ── transitions are logged (state change) ─────────────────────────────────────


def test_bucket_transition_is_logged(caplog):
    src = _fake_source("hex", "overnight")
    mon = ResourceMonitor()
    with caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.resource_monitor"):
        for _ in range(3):
            mon.record(src, 2.0)
    assert any("time_bucket" in r.message and "hex" in r.message for r in caplog.records)


# ── re-measurement changes routing (the whole point) ──────────────────────────


def test_selector_reroutes_after_remeasure():
    """A source promoted to interactive now wins an interactive call it was excluded from."""
    reg = SourceRegistry()
    slow = Source(name="hex", cost_class="owned_local")
    slow.time_bucket = "overnight"          # starts exiled from interactive work
    slow.available = True
    fast = Source(name="cloud", cost_class="token_direct")
    fast.time_bucket = "interactive"
    fast.available = True
    reg.register(slow)
    reg.register(fast)
    models = ModelsRegistry([
        ModelSpec("hex-m", "hex", "worker", 0.0, 0.0, 8192),
        ModelSpec("cloud-m", "cloud", "worker", 1.0, 1.0, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "hex-m", "hex", "hex"),
        RoutingRule(2, "worker", "cloud-m", "cloud", "cloud"),
    ]
    engine = RulesEngine(reg, models, rules)

    # Before re-measurement: hex is overnight → excluded from interactive → cloud wins.
    assert engine.route("worker", foreground=True).source.name == "cloud"

    # Re-measure hex as fast → promoted to interactive.
    mon = ResourceMonitor()
    for _ in range(3):
        mon.record(slow, 2.0)
    assert slow.time_bucket == "interactive"

    # After: hex is interactive AND cheaper (owned_local) → hex wins the same call.
    assert engine.route("worker", foreground=True).source.name == "hex"
