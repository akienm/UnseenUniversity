"""
Tests for RoutingCompiler — three pattern detectors + auto-apply path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unseen_university.devices.scraps.jobs.routing_compiler import CompileDownProposal, RoutingCompiler

# ── Helpers ───────────────────────────────────────────────────────────────────


def _entry(
    ticket_id: str,
    tags: list[str],
    size: str,
    task_class: str,
    signal: str,
    advisor_signal: str | None,
    excerpt: str = "",
) -> dict:
    return {
        "ts": "2026-06-01T00:00:00+00:00",
        "ticket_id": ticket_id,
        "tags": tags,
        "size": size,
        "task_class": task_class,
        "signal": signal,
        "advisor_signal": advisor_signal,
        "iterations": 10,
        "cost_usd": 0.05,
        "tokens_in": 500,
        "tokens_out": 200,
        "excerpt": excerpt,
    }


def _seed(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def compiler(tmp_path):
    return RoutingCompiler(
        corpus_path=tmp_path / "escalation_corpus.jsonl",
        compiled_rules_path=tmp_path / "compiled_routing_rules.json",
        reprompt_threshold=0.40,
        auto_apply_confidence=0.90,
        auto_apply_min_samples=20,
    )


# ── Pattern 1: UPGRADE_TIER ───────────────────────────────────────────────────


def test_analyze_finds_upgrade_tier_pattern(compiler, tmp_path):
    """Platform/S tickets that consistently get UPGRADE → propose analyst tier."""
    entries = [
        _entry(f"T-{i}", ["Platform"], "S", "worker", "ESCALATE: analyst", "UPGRADE")
        for i in range(8)
    ] + [
        _entry("T-none", ["Platform"], "S", "worker", "ESCALATE: worker", None),
    ]
    proposals = compiler.analyze(entries)
    upgrade = [p for p in proposals if p.kind == "UPGRADE_TIER"]
    assert len(upgrade) >= 1
    p = upgrade[0]
    assert p.tag == "Platform"
    assert p.size == "S"
    assert p.task_class == "worker"
    assert p.proposed_task_class == "analyst"
    assert p.confidence >= 0.5
    assert p.sample_count == 9


def test_upgrade_tier_below_confidence_not_proposed(compiler):
    """Low UPGRADE rate does not produce UPGRADE_TIER proposal."""
    entries = [
        _entry("T-1", ["Infrastructure"], "M", "worker", "ESCALATE: worker", "UPGRADE"),
        _entry("T-2", ["Infrastructure"], "M", "worker", "ESCALATE: worker", None),
        _entry("T-3", ["Infrastructure"], "M", "worker", "ESCALATE: worker", None),
        _entry("T-4", ["Infrastructure"], "M", "worker", "ESCALATE: worker", None),
    ]
    proposals = compiler.analyze(entries)
    upgrade = [
        p for p in proposals if p.kind == "UPGRADE_TIER" and p.tag == "Infrastructure"
    ]
    # 1/4 = 25% UPGRADE — below 50% threshold
    assert len(upgrade) == 0


# ── Pattern 2: SETUP_GAP ─────────────────────────────────────────────────────


def test_analyze_finds_setup_gap_pattern(compiler):
    """BLOCKED signals with ECONNREFUSED → flag as infrastructure gap."""
    entries = [
        _entry(
            f"T-{i}",
            ["Database"],
            "S",
            "worker",
            "ESCALATE: worker",
            "BLOCKED",
            excerpt="BLOCKED: Connection refused (ECONNREFUSED 127.0.0.1:5432)",
        )
        for i in range(5)
    ] + [
        _entry(
            "T-other",
            ["Database"],
            "S",
            "worker",
            "ESCALATE: worker",
            "BLOCKED",
            excerpt="BLOCKED: Logic error in SQL query",
        ),
    ]
    proposals = compiler.analyze(entries)
    setup = [p for p in proposals if p.kind == "SETUP_GAP"]
    assert len(setup) >= 1
    p = next(p for p in setup if p.tag == "Database")
    assert p.confidence >= 0.5
    assert "infrastructure gap" in p.detail.lower()


def test_setup_gap_no_keyword_not_proposed(compiler):
    """BLOCKED without setup keywords does not produce SETUP_GAP proposal."""
    entries = [
        _entry(
            "T-1",
            ["Cognition"],
            "S",
            "analyst",
            "ESCALATE: designer",
            "BLOCKED",
            excerpt="BLOCKED: model response did not address the task",
        ),
        _entry(
            "T-2",
            ["Cognition"],
            "S",
            "analyst",
            "ESCALATE: designer",
            "BLOCKED",
            excerpt="BLOCKED: output truncated at max tokens",
        ),
    ]
    proposals = compiler.analyze(entries)
    setup = [p for p in proposals if p.kind == "SETUP_GAP" and p.tag == "Cognition"]
    assert len(setup) == 0


# ── Pattern 3: REPROMPT_RATE ─────────────────────────────────────────────────


def test_analyze_finds_reprompt_rate_pattern(compiler):
    """Tag with REPROMPT rate > 40% threshold → template improvement proposal."""
    entries = [
        _entry(
            f"T-{i}", ["Infrastructure"], "M", "worker", "ESCALATE: worker", "REPROMPT"
        )
        for i in range(5)
    ] + [
        _entry("T-done", ["Infrastructure"], "M", "worker", "ESCALATE: worker", None),
        _entry("T-done2", ["Infrastructure"], "M", "worker", "ESCALATE: worker", None),
    ]
    proposals = compiler.analyze(entries)
    reprompt = [p for p in proposals if p.kind == "REPROMPT_RATE"]
    assert len(reprompt) >= 1
    p = next(p for p in reprompt if p.tag == "Infrastructure")
    assert p.confidence > 0.40
    assert "template" in p.detail.lower()


def test_reprompt_rate_below_threshold_not_proposed(compiler):
    """Low REPROMPT rate does not produce proposal."""
    entries = [
        _entry("T-1", ["Platform"], "S", "worker", "ESCALATE: worker", "REPROMPT"),
        _entry("T-2", ["Platform"], "S", "worker", "ESCALATE: worker", None),
        _entry("T-3", ["Platform"], "S", "worker", "ESCALATE: worker", None),
        _entry("T-4", ["Platform"], "S", "worker", "ESCALATE: worker", None),
        _entry("T-5", ["Platform"], "S", "worker", "ESCALATE: worker", None),
    ]
    proposals = compiler.analyze(entries)
    reprompt = [
        p for p in proposals if p.kind == "REPROMPT_RATE" and p.tag == "Platform"
    ]
    # 1/5 = 20% — below 40% threshold
    assert len(reprompt) == 0


# ── Auto-apply path ───────────────────────────────────────────────────────────


def test_auto_apply_writes_compiled_rule_for_high_confidence(compiler, tmp_path):
    """End-to-end: high-confidence UPGRADE_TIER proposal → compiled rule written."""
    compiled_rules_path = tmp_path / "compiled_routing_rules.json"

    # 22 UPGRADE escalations out of 22 total (100% confidence, above 20-sample threshold)
    high_conf_proposal = CompileDownProposal(
        kind="UPGRADE_TIER",
        tag="Platform",
        size="S",
        task_class="worker",
        confidence=0.95,
        sample_count=22,
        detail="22/22 escalations are UPGRADE — route Platform/S directly to analyst",
        proposed_task_class="analyst",
    )
    assert high_conf_proposal.auto_apply_eligible

    compiler.apply_proposal(high_conf_proposal, compiled_rules_path)

    assert compiled_rules_path.exists()
    rules = json.loads(compiled_rules_path.read_text())
    assert "Platform/S" in rules
    rule = rules["Platform/S"]
    assert rule["from_task_class"] == "worker"
    assert rule["to_task_class"] == "analyst"
    assert rule["confidence"] == pytest.approx(0.95)
    assert rule["sample_count"] == 22


def test_auto_apply_accumulates_multiple_rules(compiler, tmp_path):
    """Multiple apply_proposal calls accumulate rules in the sidecar without overwriting."""
    compiled_rules_path = tmp_path / "compiled_routing_rules.json"

    p1 = CompileDownProposal(
        kind="UPGRADE_TIER",
        tag="Platform",
        size="S",
        task_class="worker",
        confidence=0.95,
        sample_count=25,
        detail="test",
        proposed_task_class="analyst",
    )
    p2 = CompileDownProposal(
        kind="UPGRADE_TIER",
        tag="Database",
        size="M",
        task_class="analyst",
        confidence=0.92,
        sample_count=21,
        detail="test",
        proposed_task_class="designer",
    )
    compiler.apply_proposal(p1, compiled_rules_path)
    compiler.apply_proposal(p2, compiled_rules_path)

    rules = json.loads(compiled_rules_path.read_text())
    assert "Platform/S" in rules
    assert "Database/M" in rules


def test_apply_proposal_skips_non_upgrade_tier(compiler, tmp_path):
    """SETUP_GAP and REPROMPT_RATE proposals are not written by apply_proposal."""
    compiled_rules_path = tmp_path / "compiled_routing_rules.json"

    setup_gap = CompileDownProposal(
        kind="SETUP_GAP",
        tag="Database",
        size="*",
        task_class="*",
        confidence=0.95,
        sample_count=25,
        detail="infrastructure gap",
    )
    compiler.apply_proposal(setup_gap, compiled_rules_path)
    assert not compiled_rules_path.exists()


def test_auto_apply_below_threshold_not_eligible(compiler):
    """Proposals below confidence OR sample threshold are not auto-apply eligible."""
    low_conf = CompileDownProposal(
        kind="UPGRADE_TIER",
        tag="Platform",
        size="S",
        task_class="worker",
        confidence=0.85,
        sample_count=25,
        detail="below confidence",
        proposed_task_class="analyst",
    )
    low_n = CompileDownProposal(
        kind="UPGRADE_TIER",
        tag="Platform",
        size="S",
        task_class="worker",
        confidence=0.95,
        sample_count=15,
        detail="below sample count",
        proposed_task_class="analyst",
    )
    assert not low_conf.auto_apply_eligible
    assert not low_n.auto_apply_eligible


# ── Load corpus ───────────────────────────────────────────────────────────────


def test_load_corpus_returns_entries(compiler, tmp_path):
    corpus_path = tmp_path / "escalation_corpus.jsonl"
    entries = [
        _entry(f"T-{i}", ["Platform"], "S", "worker", "ESCALATE: worker", None)
        for i in range(5)
    ]
    _seed(corpus_path, entries)

    loaded = compiler.load_corpus()
    assert len(loaded) == 5
    assert loaded[0]["ticket_id"] == "T-0"


def test_load_corpus_missing_file_returns_empty(compiler):
    loaded = compiler.load_corpus()
    assert loaded == []


# ── RulesEngine.add_compiled_rule integration ─────────────────────────────────


def test_rules_engine_add_compiled_rule():
    """add_compiled_rule prepends a new rule and it takes priority in routing."""
    from unittest.mock import MagicMock

    from unseen_university.devices.inference.models_registry import ModelsRegistry, ModelSpec
    from unseen_university.devices.inference.rules_engine import RoutingRule, RulesEngine
    from unseen_university.devices.inference.sources import Source, SourceRegistry

    sources = SourceRegistry()
    src = MagicMock(spec=Source)
    src.name = "openrouter"
    src.available = True
    sources.register(src)

    models = ModelsRegistry()
    spec = ModelSpec(
        model_id="deepseek/deepseek-v3",
        tier="analyst",
        source_name="openrouter",
        input_cost_per_1m=1.4,
        output_cost_per_1m=2.8,
        context_window=131072,
    )
    models.register(spec)

    engine = RulesEngine(sources, models, rules=[])

    compiled = RoutingRule(
        priority=0,
        task_class="analyst",
        model_id="deepseek/deepseek-v3",
        source_name="openrouter",
        label="compiled:Platform/S→analyst",
    )
    engine.add_compiled_rule(compiled)

    decision = engine.route("analyst")
    assert decision is not None
    assert decision.rule_label == "compiled:Platform/S→analyst"
