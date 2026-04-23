"""tests/test_trace_miss_report.py — turn_id → retrieval-miss report.

Uses injected loaders (no live MCP) to verify:
- report shape populated from turn + traces + memories
- confab scan integrates and surfaces in report
- miss flags fire for known shapes (stale memories, empty retrieval,
  confab+no-FACTUAL, etc.)
- suggested engram shape follows confab subtype
- 897ad9c0 fixture from 2026-04-23 produces the expected flags
"""

from __future__ import annotations

from datetime import datetime, timezone

from lab.claudecode.engram_tools.trace_miss_report import (
    STALE_ACTIVATION_DAYS,
    ActivatedMemory,
    TraceMissAnalyzer,
    TraceMissReport,
    render_report,
)

# ── shared helpers ───────────────────────────────────────────────────────────


NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)


def _analyzer(turns, traces_by_ts, memories):
    turns_by_id = {t["turn_id"]: t for t in turns}
    mems_by_id = {m["id"]: m for m in memories}
    return TraceMissAnalyzer(
        turn_loader=lambda tid: turns_by_id.get(tid),
        trace_loader=lambda ts: traces_by_ts.get(ts, []),
        memory_loader=lambda mid: mems_by_id.get(mid),
        current_year=2026,
        now=NOW,
    )


# Fixtures modeled on the real 2026-04-23 897ad9c0 case


TURN_897 = {
    "turn_id": "897ad9c0",
    "timestamp": "2026-04-23T06:51:24",
    "in": "Except, you're not doing the tickets you're picking up?",
    "out": (
        "Honest answer: things are tangled. No autonomous tool-call loop — "
        "when I'm in the web channel, I can't actually execute code."
    ),
    "intent": "complaint",
    "bg": "WINNOW_BCEF4ADCB6",
    "tier": "tier.2",
}

TRACE_897 = {
    "2026-04-23T06:51:24": [
        {
            "query": "user complaint context",
            "top_nodes": [
                {"memory_id": "20260324175437948742", "relevance": 0.74},
                {"memory_id": "20260319142631259031", "relevance": 0.73},
            ],
        },
        {
            "query": "recent exchanges channel",
            "top_nodes": [
                {"memory_id": "20260422162449488770", "relevance": 0.72},
            ],
        },
    ]
}

MEMORIES_897 = [
    {
        "id": "20260324175437948742",
        "memory_type": "FACTUAL",
        "narrative": "The user expressed a complaint regarding Kindle 2FA…",
        "metadata": {"deposited_at": "2026-03-24T17:54:37"},
    },
    {
        "id": "20260319142631259031",
        "memory_type": "FACTUAL",
        "narrative": "Users may occasionally send corrections rather than complaints…",
        "metadata": {"deposited_at": "2026-03-19T14:26:31"},
    },
    {
        "id": "20260422162449488770",
        "memory_type": "FACTUAL",
        "narrative": "TASK_SET|Good morning / Good morning",
        "metadata": {"deposited_at": "2026-04-22T10:24:49"},
    },
]


# ── basic shape ──────────────────────────────────────────────────────────────


class TestReportShape:
    def test_returns_none_when_turn_not_found(self):
        an = _analyzer([], {}, [])
        assert an.analyze("nonexistent") is None

    def test_populates_core_fields_from_turn(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert r is not None
        assert r.turn_id == "897ad9c0"
        assert r.intent == "complaint"
        assert r.tier == "tier.2"
        assert r.bg_top_habit == "WINNOW_BCEF4ADCB6"
        assert "not doing the tickets" in r.input_preview
        assert "web channel" in r.output_preview

    def test_populates_cortex_queries(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert len(r.cortex_queries) == 2
        assert "user complaint context" in r.cortex_queries
        assert "recent exchanges channel" in r.cortex_queries

    def test_populates_activated_memories(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert len(r.activated_memories) == 3
        ids = {m.memory_id for m in r.activated_memories}
        assert "20260324175437948742" in ids


# ── confab integration ──────────────────────────────────────────────────────


class TestConfabIntegration:
    def test_confab_matches_surface_in_report(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert len(r.confab_matches) >= 2
        subtypes = {m.subtype for m in r.confab_matches}
        assert "capability" in subtypes
        assert "self" in subtypes

    def test_clean_turn_has_no_confab_matches(self):
        turn = {
            "turn_id": "clean1",
            "timestamp": "2026-04-23T10:00:00",
            "in": "Claim the next ticket.",
            "out": "Claimed T-foo. Starting work.",
            "intent": "command",
            "tier": "tier.2",
        }
        an = _analyzer([turn], {}, [])
        r = an.analyze("clean1")
        assert r.confab_matches == []


# ── age computation ─────────────────────────────────────────────────────────


class TestActivationAge:
    def test_age_days_computed_from_deposited_at(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        by_id = {m.memory_id: m for m in r.activated_memories}
        # 2026-04-23 12:00Z minus 2026-03-24 17:54Z = 29 full days
        assert by_id["20260324175437948742"].age_days == 29
        # 2026-04-22 to 2026-04-23 = 1 day
        assert by_id["20260422162449488770"].age_days == 1

    def test_missing_deposited_at_yields_none_age(self):
        mem = {"id": "m1", "memory_type": "FACTUAL", "narrative": "x"}
        turn = {
            "turn_id": "t1",
            "timestamp": "2026-04-23T10:00:00",
            "out": "",
        }
        traces = {
            "2026-04-23T10:00:00": [
                {"query": "q", "top_nodes": [{"memory_id": "m1", "relevance": 0.5}]}
            ]
        }
        an = _analyzer([turn], traces, [mem])
        r = an.analyze("t1")
        assert r.activated_memories[0].age_days is None


# ── miss flags ──────────────────────────────────────────────────────────────


class TestMissFlags:
    def test_897_flags_confab_subtypes(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert any("confabulation tells detected" in f for f in r.miss_flags)

    def test_897_flags_stale_activations(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        stale_flags = [f for f in r.miss_flags if "stale" in f]
        assert len(stale_flags) >= 1

    def test_897_flags_intent_plus_confab(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert any("intent=complaint" in f for f in r.miss_flags)

    def test_empty_retrieval_flagged(self):
        turn = {
            "turn_id": "t_empty",
            "timestamp": "2026-04-23T10:00:00",
            "in": "q",
            "out": "",
            "intent": "general",
        }
        traces = {"2026-04-23T10:00:00": [{"query": "q1", "top_nodes": []}]}
        an = _analyzer([turn], traces, [])
        r = an.analyze("t_empty")
        assert any("retrieval empty" in f for f in r.miss_flags)

    def test_all_stale_flagged_stronger_than_partial(self):
        old_mem = {
            "id": "old1",
            "memory_type": "FACTUAL",
            "narrative": "x",
            "metadata": {"deposited_at": "2025-01-01T00:00:00"},
        }
        turn = {
            "turn_id": "t_stale",
            "timestamp": "2026-04-23T10:00:00",
            "out": "I don't have direct access to fetch that.",
            "intent": "general",
        }
        traces = {
            "2026-04-23T10:00:00": [
                {"query": "q", "top_nodes": [{"memory_id": "old1", "relevance": 0.5}]}
            ]
        }
        an = _analyzer([turn], traces, [old_mem])
        r = an.analyze("t_stale")
        assert any("all 1 activated memories are stale" in f for f in r.miss_flags)

    def test_no_factual_memory_during_confab_flagged(self):
        """A confab turn with zero FACTUAL anchors is a grounding gap."""
        epi_mem = {
            "id": "e1",
            "memory_type": "EPISODIC",
            "narrative": "a memory of a past event",
            "metadata": {"deposited_at": "2026-04-20T00:00:00"},
        }
        turn = {
            "turn_id": "t_nofactual",
            "timestamp": "2026-04-23T10:00:00",
            "out": "I don't have direct access to that.",
            "intent": "general",
        }
        traces = {
            "2026-04-23T10:00:00": [
                {"query": "q", "top_nodes": [{"memory_id": "e1", "relevance": 0.5}]}
            ]
        }
        an = _analyzer([turn], traces, [epi_mem])
        r = an.analyze("t_nofactual")
        assert any("no FACTUAL memories activated" in f for f in r.miss_flags)


# ── suggested engram shape ──────────────────────────────────────────────────


class TestSuggestEngram:
    def test_capability_confab_suggests_capability_engram(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        assert r.suggested_engram_shape is not None
        assert (
            "capability" in r.suggested_engram_shape.lower()
            or "channels are transports" in r.suggested_engram_shape
        )

    def test_fact_only_confab_suggests_fact_engram(self):
        turn = {
            "turn_id": "t_fact",
            "timestamp": "2026-04-23T10:00:00",
            "out": "That was April 2025.",
            "intent": "general",
        }
        an = _analyzer([turn], {}, [])
        r = an.analyze("t_fact")
        assert r.suggested_engram_shape is not None
        assert "current-state fact" in r.suggested_engram_shape

    def test_no_confab_no_suggestion(self):
        turn = {
            "turn_id": "t_clean",
            "timestamp": "2026-04-23T10:00:00",
            "out": "ok",
            "intent": "command",
        }
        an = _analyzer([turn], {}, [])
        r = an.analyze("t_clean")
        assert r.suggested_engram_shape is None


# ── render ──────────────────────────────────────────────────────────────────


class TestRender:
    def test_render_includes_all_sections(self):
        an = _analyzer([TURN_897], TRACE_897, MEMORIES_897)
        r = an.analyze("897ad9c0")
        rendered = render_report(r)
        assert "897ad9c0" in rendered
        assert "cortex queries" in rendered
        assert "activated memories" in rendered
        assert "confabulation tells" in rendered
        assert "miss flags" in rendered
        assert "suggested engram" in rendered


# ── stale threshold constant sanity ──────────────────────────────────────────


class TestStaleThreshold:
    def test_constant_exported(self):
        assert STALE_ACTIVATION_DAYS > 0

    def test_memory_exactly_at_threshold_flagged_as_stale(self):
        exactly_stale_date = NOW.replace(tzinfo=timezone.utc)
        from datetime import timedelta

        deposited = exactly_stale_date - timedelta(days=STALE_ACTIVATION_DAYS)
        mem = {
            "id": "mstale",
            "memory_type": "FACTUAL",
            "narrative": "x",
            "metadata": {"deposited_at": deposited.isoformat()},
        }
        turn = {
            "turn_id": "t_exact",
            "timestamp": "2026-04-23T10:00:00",
            "out": "",
            "intent": "general",
        }
        traces = {
            "2026-04-23T10:00:00": [
                {"query": "q", "top_nodes": [{"memory_id": "mstale", "relevance": 0.5}]}
            ]
        }
        an = _analyzer([turn], traces, [mem])
        r = an.analyze("t_exact")
        assert any("stale" in f for f in r.miss_flags)
