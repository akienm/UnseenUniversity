"""Tests for confidence-gated palace writes (PalaceWriter)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
import pytest

from unseen_university.devices.librarian.palace_writer import (
    PalaceWriter,
    PalaceWriteRequest,
    ProvenanceEntry,
    WriteResult,
    LIBRARIAN_AUTHORS,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _req(
    path="test/node",
    title="Test",
    content="content",
    sources: list[tuple[str, str, float]] | None = None,
    confidence: float = 0.9,
) -> PalaceWriteRequest:
    if sources is None:
        sources = [("url", "https://example.com", 0.9)]
    provenance = [
        ProvenanceEntry(source_type=st, source=s, confidence=c) for st, s, c in sources
    ]
    return PalaceWriteRequest(
        path=path,
        title=title,
        content=content,
        provenance_chain=provenance,
        confidence_score=confidence,
    )


def _writer(
    *,
    human_authored: bool = False,
    principals: dict[str, int] | None = None,
    inbox_calls: list[dict] | None = None,
) -> PalaceWriter:
    """Return a PalaceWriter with patched internals — no real DB needed."""
    calls = inbox_calls if inbox_calls is not None else []
    writer = PalaceWriter(
        pg_url="postgresql://mock/mock",
        cc_inbox_fn=lambda **kw: calls.append(kw),
    )
    writer._principals = principals if principals is not None else {}
    writer._is_human_authored = MagicMock(return_value=human_authored)
    writer._do_write = MagicMock()
    return writer


# ── ProvenanceEntry clamping ───────────────────────────────────────────────────


class TestProvenanceEntry:
    def test_confidence_clamped_above(self):
        e = ProvenanceEntry(source_type="url", source="x", confidence=1.5)
        assert e.confidence == 0.999

    def test_confidence_clamped_below(self):
        e = ProvenanceEntry(source_type="url", source="x", confidence=-0.1)
        assert e.confidence == 0.0

    def test_confidence_within_range(self):
        e = ProvenanceEntry(source_type="url", source="x", confidence=0.75)
        assert e.confidence == 0.75


class TestPalaceWriteRequest:
    def test_confidence_clamped_to_999(self):
        req = _req(confidence=1.0)
        assert req.confidence_score == 0.999

    def test_confidence_preserved_below_cap(self):
        req = _req(confidence=0.8)
        assert req.confidence_score == 0.8


# ── Tier evaluation ────────────────────────────────────────────────────────────


class TestTierEvaluation:
    def test_rejected_when_zero_sources(self):
        writer = _writer()
        req = PalaceWriteRequest(
            path="test/node",
            title="T",
            content="c",
            provenance_chain=[],
            confidence_score=0.9,
        )
        result = writer.write(req)
        assert result.tier == "rejected"
        assert not result.written
        writer._do_write.assert_not_called()

    def test_rejected_when_zero_confidence(self):
        writer = _writer()
        result = writer.write(_req(confidence=0.0))
        assert result.tier == "rejected"
        assert not result.written
        writer._do_write.assert_not_called()

    def test_low_tier_one_source(self):
        writer = _writer()
        result = writer.write(_req(sources=[("url", "x", 0.9)], confidence=0.4))
        assert result.tier == "low"
        assert result.written
        writer._do_write.assert_called_once()

    def test_medium_tier_two_sources(self):
        writer = _writer()
        result = writer.write(
            _req(
                sources=[("url", "a", 0.8), ("db_query", "SELECT 1", 0.7)],
                confidence=0.6,
            )
        )
        assert result.tier == "medium"
        assert result.written
        writer._do_write.assert_called_once()

    def test_high_tier_escalates_not_writes(self):
        inbox: list[dict] = []
        writer = _writer(inbox_calls=inbox)
        sources = [("url", f"https://{i}.com", 0.9) for i in range(5)]
        result = writer.write(_req(sources=sources, confidence=0.85))
        assert result.tier == "high_pending"
        assert not result.written
        writer._do_write.assert_not_called()
        assert inbox and inbox[0]["kind"] == "librarian_high_tier_write"

    def test_protected_human_authored_node(self):
        inbox: list[dict] = []
        writer = _writer(human_authored=True, inbox_calls=inbox)
        result = writer.write(_req(confidence=0.9))
        assert result.tier == "protected"
        assert not result.written
        writer._do_write.assert_not_called()
        assert inbox and inbox[0]["kind"] == "librarian_protected_write"

    def test_librarian_authored_node_not_protected(self):
        for author in LIBRARIAN_AUTHORS:
            inbox: list[dict] = []
            # human_authored=False because updated_by IS a librarian author
            writer = _writer(human_authored=False, inbox_calls=inbox)
            result = writer.write(_req(sources=[("url", "x", 0.9)], confidence=0.4))
            assert (
                result.tier != "protected"
            ), f"author={author} should not be protected"
            writer._do_write.assert_called_once()

    def test_boundary_medium_exactly_two_sources(self):
        writer = _writer()
        result = writer.write(
            _req(
                sources=[("url", "a", 0.9), ("url", "b", 0.9)],
                confidence=0.5,
            )
        )
        assert result.tier == "medium"

    def test_boundary_high_exactly_five_sources(self):
        inbox: list[dict] = []
        writer = _writer(inbox_calls=inbox)
        sources = [("url", f"s{i}", 0.9) for i in range(5)]
        result = writer.write(_req(sources=sources, confidence=0.8))
        assert result.tier == "high_pending"
        assert inbox

    def test_effective_sources_in_result(self):
        writer = _writer()
        sources = [("url", "a", 0.9), ("url", "b", 0.8)]
        result = writer.write(_req(sources=sources, confidence=0.6))
        assert result.effective_sources == 2.0


# ── Trust hierarchy (principal multiplier) ────────────────────────────────────


class TestTrustHierarchy:
    def test_principal_multiplier_boosts_effective_sources(self):
        writer = PalaceWriter(pg_url="postgresql://mock/mock")
        writer._principals = {"akien": 10}
        entry = ProvenanceEntry(
            source_type="principal_assertion", source="Akien", confidence=0.9
        )
        assert writer._effective_sources([entry]) == 10.0

    def test_unknown_principal_defaults_to_1(self):
        writer = PalaceWriter(pg_url="postgresql://mock/mock")
        writer._principals = {"akien": 10}
        entry = ProvenanceEntry(
            source_type="principal_assertion", source="stranger", confidence=0.9
        )
        assert writer._effective_sources([entry]) == 1.0

    def test_akien_assertion_clears_medium_tier(self):
        """Akien (multiplier=10) alone → effective=10 → clears MEDIUM (>=2)."""
        writer = _writer(principals={"akien": 10})
        result = writer.write(
            _req(
                sources=[("principal_assertion", "Akien", 0.9)],
                confidence=0.6,
            )
        )
        assert result.tier == "medium"
        assert result.written

    def test_akien_assertion_escalates_high_tier(self):
        """Akien alone → effective=10 ≥5 AND confidence ≥0.8 → HIGH escalation."""
        inbox: list[dict] = []
        writer = _writer(principals={"akien": 10}, inbox_calls=inbox)
        result = writer.write(
            _req(
                sources=[("principal_assertion", "Akien", 0.9)],
                confidence=0.85,
            )
        )
        assert result.tier == "high_pending"
        assert not result.written
        assert inbox

    def test_load_principals_parses_regex(self):
        doc = (
            "### Akien\n"
            "Primary human.\n"
            "**credibility_multiplier:** 10\n\n"
            "### TestUser\n"
            "Another user.\n"
            "**credibility_multiplier:** 3\n"
        )
        writer = PalaceWriter(pg_url="postgresql://mock/mock")
        mock_conn_ctx = MagicMock()
        mock_conn = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (doc,)
        writer._conn = MagicMock(return_value=mock_conn_ctx)

        principals = writer._load_principals()
        assert principals.get("akien") == 10
        assert principals.get("testuser") == 3

    def test_load_principals_returns_empty_on_missing_node(self):
        writer = PalaceWriter(pg_url="postgresql://mock/mock")
        mock_conn_ctx = MagicMock()
        mock_conn = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = None
        writer._conn = MagicMock(return_value=mock_conn_ctx)

        assert writer._load_principals() == {}


# ── Behavioral observation emission ───────────────────────────────────────────


class TestEmitBehavioralObservation:
    def _patched_writer(self) -> tuple[PalaceWriter, list[tuple]]:
        """Return writer with emit_behavioral_observation patched to record calls."""
        calls: list[tuple] = []
        writer = _writer()
        writer.emit_behavioral_observation = MagicMock(
            side_effect=lambda **kw: calls.append(kw)
        )
        return writer, calls

    def test_write_low_emits_answered(self):
        writer, calls = self._patched_writer()
        writer.write(_req(sources=[("url", "x", 0.9)], confidence=0.4))
        assert calls, "emit_behavioral_observation not called"
        assert calls[0]["outcome"] == "answered"
        assert calls[0]["tier"] == "low"

    def test_write_rejected_emits_failed(self):
        writer, calls = self._patched_writer()
        writer.write(_req(confidence=0.0))
        assert calls[0]["outcome"] == "failed"
        assert calls[0]["tier"] == "rejected"

    def test_write_protected_emits_escalated(self):
        inbox: list[dict] = []
        writer = _writer(human_authored=True, inbox_calls=inbox)
        writer.emit_behavioral_observation = MagicMock()
        writer.write(_req(confidence=0.9))
        writer.emit_behavioral_observation.assert_called_once()
        kw = writer.emit_behavioral_observation.call_args.kwargs
        assert kw["outcome"] == "escalated"
        assert kw["tier"] == "protected"

    def test_write_high_pending_emits_escalated(self):
        inbox: list[dict] = []
        writer = _writer(inbox_calls=inbox)
        writer.emit_behavioral_observation = MagicMock()
        sources = [("url", f"s{i}", 0.9) for i in range(5)]
        writer.write(_req(sources=sources, confidence=0.85))
        writer.emit_behavioral_observation.assert_called_once()
        kw = writer.emit_behavioral_observation.call_args.kwargs
        assert kw["outcome"] == "escalated"
        assert kw["tier"] == "high_pending"

    def test_write_low_passes_path_as_topic(self):
        writer, calls = self._patched_writer()
        writer.write(_req(path="some/node", confidence=0.4))
        assert calls[0]["topic"] == "some/node"
