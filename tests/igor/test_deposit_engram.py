"""tests/test_deposit_engram.py — grounding engram deposit tool.

Tests validation, shape-enforcement, and cortex-stub integration. Does not
hit a live Postgres — uses an in-memory stub for cortex.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from lab.claudecode.engram_tools.deposit_engram import (
    GroundingEngram,
    ValidationError,
    _validate,
    build_memory,
    deposit,
)


@dataclass
class _StubCortex:
    """Capture-only cortex stub. Assigns an id on store and keeps the memory."""

    stored: list = field(default_factory=list)

    def store(self, memory):
        if not memory.id:
            memory.id = f"m{len(self.stored) + 1:03d}"
        self.stored.append(memory)
        return memory


# ── happy path ──────────────────────────────────────────────────────────────


class TestDepositHappyPath:
    def test_deposit_returns_memory_id(self):
        engram = GroundingEngram(
            narrative="Igor tools are channel-agnostic. The web channel is a transport, not a capability gate.",
            anchor_keywords=["channel", "transport", "tools"],
            grounding_domain="capability/channels",
        )
        cortex = _StubCortex()
        mid = deposit(engram, cortex=cortex)
        assert mid
        assert len(cortex.stored) == 1

    def test_deposited_memory_has_factual_type(self):
        from wild_igor.igor.memory.models import MemoryType

        engram = GroundingEngram(
            narrative="Today is 2026-04-23. Igor's clock reads 2026.",
            anchor_keywords=["2026", "clock"],
            grounding_domain="fact/current_date",
        )
        cortex = _StubCortex()
        deposit(engram, cortex=cortex)
        assert cortex.stored[0].memory_type == MemoryType.FACTUAL

    def test_deposited_memory_carries_grounding_metadata(self):
        engram = GroundingEngram(
            narrative="Igor is a graph matrix reasoning engine, not a language model.",
            anchor_keywords=["graph", "reasoning", "engine"],
            grounding_domain="self/identity",
        )
        cortex = _StubCortex()
        deposit(engram, cortex=cortex)
        mem = cortex.stored[0]
        assert mem.metadata["grounding_domain"] == "self/identity"
        assert mem.metadata["anchor_keywords"] == ["graph", "reasoning", "engine"]
        assert mem.metadata["deposited_by"] == "engram_tool"
        assert "deposited_at" in mem.metadata

    def test_parent_cp_sets_parent_id(self):
        engram = GroundingEngram(
            narrative="Anchor text with channel and tools mentioned.",
            anchor_keywords=["channel", "tools"],
            grounding_domain="capability/channels",
            parent_cp="CP1",
        )
        cortex = _StubCortex()
        deposit(engram, cortex=cortex)
        assert cortex.stored[0].parent_id == "CP1"

    def test_custom_source_propagates(self):
        engram = GroundingEngram(
            narrative="foo bar baz",
            anchor_keywords=["foo"],
            grounding_domain="x",
            source="test_seeder",
        )
        cortex = _StubCortex()
        deposit(engram, cortex=cortex)
        assert cortex.stored[0].source == "test_seeder"
        assert cortex.stored[0].metadata["deposited_by"] == "test_seeder"

    def test_extra_metadata_merges(self):
        engram = GroundingEngram(
            narrative="alpha beta",
            anchor_keywords=["alpha"],
            grounding_domain="x",
            extra_metadata={"note": "provisional", "ticket": "T-xyz"},
        )
        cortex = _StubCortex()
        deposit(engram, cortex=cortex)
        md = cortex.stored[0].metadata
        assert md["note"] == "provisional"
        assert md["ticket"] == "T-xyz"
        # original fields still there
        assert md["grounding_domain"] == "x"


# ── validation ──────────────────────────────────────────────────────────────


class TestValidation:
    def test_empty_narrative_rejected(self):
        engram = GroundingEngram(
            narrative="",
            anchor_keywords=["x"],
            grounding_domain="d",
        )
        with pytest.raises(ValidationError, match="narrative"):
            _validate(engram)

    def test_whitespace_only_narrative_rejected(self):
        engram = GroundingEngram(
            narrative="   \n  ",
            anchor_keywords=["x"],
            grounding_domain="d",
        )
        with pytest.raises(ValidationError, match="narrative"):
            _validate(engram)

    def test_empty_anchor_keywords_rejected(self):
        engram = GroundingEngram(
            narrative="a narrative",
            anchor_keywords=[],
            grounding_domain="d",
        )
        with pytest.raises(ValidationError, match="anchor_keywords"):
            _validate(engram)

    def test_empty_grounding_domain_rejected(self):
        engram = GroundingEngram(
            narrative="some narrative with anchor",
            anchor_keywords=["anchor"],
            grounding_domain="",
        )
        with pytest.raises(ValidationError, match="grounding_domain"):
            _validate(engram)

    def test_confidence_out_of_range_rejected(self):
        engram = GroundingEngram(
            narrative="anchor text",
            anchor_keywords=["anchor"],
            grounding_domain="d",
            confidence=1.5,
        )
        with pytest.raises(ValidationError, match="confidence"):
            _validate(engram)

    def test_anchor_keyword_missing_from_narrative_rejected(self):
        engram = GroundingEngram(
            narrative="This narrative only mentions foo.",
            anchor_keywords=["foo", "bar"],
            grounding_domain="d",
        )
        with pytest.raises(ValidationError, match="anchor keywords missing"):
            _validate(engram)

    def test_keyword_match_is_case_insensitive(self):
        engram = GroundingEngram(
            narrative="This says CHANNEL and Tools.",
            anchor_keywords=["channel", "tools"],
            grounding_domain="d",
        )
        _validate(engram)  # should not raise

    def test_deposit_raises_on_validation_failure(self):
        engram = GroundingEngram(
            narrative="no matching keyword here",
            anchor_keywords=["zebra"],
            grounding_domain="d",
        )
        cortex = _StubCortex()
        with pytest.raises(ValidationError):
            deposit(engram, cortex=cortex)
        assert cortex.stored == []


# ── build_memory shape ──────────────────────────────────────────────────────


class TestBuildMemory:
    def test_build_memory_returns_memory_with_correct_fields(self):
        from wild_igor.igor.memory.models import MemoryType

        engram = GroundingEngram(
            narrative="Igor's tools are channel-agnostic transports.",
            anchor_keywords=["tools", "channel"],
            grounding_domain="capability/channels",
            confidence=0.9,
        )
        mem = build_memory(engram)
        assert mem.memory_type == MemoryType.FACTUAL
        assert mem.narrative == engram.narrative
        assert mem.confidence == 0.9
        assert "engram_tool" in mem.context_of_encoding
        assert "capability/channels" in mem.context_of_encoding
