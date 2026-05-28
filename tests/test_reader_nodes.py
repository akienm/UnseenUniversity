"""
Tests for ReaderDevice nodes mode (T-reader-node-mode).

Inference is mocked — no LLM calls, no network. Tests verify:
  - extract_nodes returns list of dicts with required keys
  - node narrative/memory_type/metadata shape correct
  - confidence filter at 0.60 is applied
  - unknown node types fall back to FACTUAL
  - JSON parse errors yield empty list for that chunk (no crash)
  - binary content (empty FetchResult.content) → empty list, no error
  - ReaderDevice.read(uri, format='nodes') routes correctly
  - format='summary' still works after node mode added (no regression)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.reader.node_extractor import _extract_chunk, extract_nodes

# ── _extract_chunk unit tests ─────────────────────────────────────────────────


def _mock_inf(json_reply: str) -> MagicMock:
    resp = MagicMock()
    resp.text = json_reply
    resp.model = "qwen2.5:7b"
    inf = MagicMock()
    inf.dispatch.return_value = resp
    return inf


class TestExtractChunk:
    def test_returns_factual_node(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "Water is H2O.", "confidence": 0.9}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert len(nodes) == 1
        assert nodes[0]["memory_type"] == "FACTUAL"
        assert nodes[0]["narrative"] == "Water is H2O."

    def test_returns_interpretive_node(self):
        reply = '{"nodes": [{"type": "interpretive", "narrative": "When X, Y follows.", "confidence": 0.75}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes[0]["memory_type"] == "INTERPRETIVE"

    def test_returns_procedural_node(self):
        reply = '{"nodes": [{"type": "procedural", "narrative": "Do A then B.", "confidence": 0.8}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes[0]["memory_type"] == "PROCEDURAL"

    def test_mechanism_maps_to_interpretive(self):
        reply = '{"nodes": [{"type": "mechanism", "narrative": "A causes B.", "confidence": 0.8}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes[0]["memory_type"] == "INTERPRETIVE"

    def test_unknown_type_maps_to_factual(self):
        reply = '{"nodes": [{"type": "banana", "narrative": "Something.", "confidence": 0.8}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes[0]["memory_type"] == "FACTUAL"

    def test_confidence_below_threshold_filtered(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "Low confidence.", "confidence": 0.5}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes == []

    def test_confidence_at_threshold_included(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "At threshold.", "confidence": 0.60}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert len(nodes) == 1

    def test_json_parse_error_returns_empty(self):
        inf = _mock_inf("NOT VALID JSON {{{")
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", inf)
        assert nodes == []

    def test_inference_error_returns_empty(self):
        inf = MagicMock()
        inf.dispatch.side_effect = RuntimeError("LLM down")
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", inf)
        assert nodes == []

    def test_metadata_keys_present(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "A fact.", "confidence": 0.85}]}'
        nodes = _extract_chunk(
            "Some text.",
            2,
            "https://example.com/",
            _mock_inf(reply),
            source_title="My Book",
            source_author="Author Name",
        )
        meta = nodes[0]["metadata"]
        assert meta["source_uri"] == "https://example.com/"
        assert meta["chunk_position"] == 2
        assert meta["source_title"] == "My Book"
        assert meta["source_author"] == "Author Name"
        assert "model_used" in meta
        assert "extracted_at" in meta
        assert "confidence" in meta
        assert "node_type" in meta

    def test_empty_narrative_filtered(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "", "confidence": 0.9}]}'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert nodes == []

    def test_markdown_fenced_json_parsed(self):
        reply = '```json\n{"nodes": [{"type": "factual", "narrative": "A fact.", "confidence": 0.7}]}\n```'
        nodes = _extract_chunk("Some text.", 0, "file:///x.txt", _mock_inf(reply))
        assert len(nodes) == 1


# ── extract_nodes (multi-chunk) ───────────────────────────────────────────────


class TestExtractNodes:
    def _mock_inf_n(self, replies: list[str]) -> MagicMock:
        call_count = [0]

        def side(req):
            resp = MagicMock()
            resp.model = "qwen2.5:7b"
            resp.text = replies[call_count[0] % len(replies)]
            call_count[0] += 1
            return resp

        inf = MagicMock()
        inf.dispatch.side_effect = side
        return inf

    def test_two_chunks_produce_combined_nodes(self):
        reply = '{"nodes": [{"type": "factual", "narrative": "A fact.", "confidence": 0.8}]}'
        inf = self._mock_inf_n([reply, reply])
        nodes = extract_nodes(["chunk one", "chunk two"], "file:///x.txt", inf)
        assert len(nodes) == 2

    def test_empty_chunks_list_returns_empty(self):
        inf = MagicMock()
        nodes = extract_nodes([], "file:///x.txt", inf)
        assert nodes == []
        inf.dispatch.assert_not_called()

    def test_chunk_positions_sequential(self):
        reply = (
            '{"nodes": [{"type": "factual", "narrative": "Fact.", "confidence": 0.8}]}'
        )
        inf = self._mock_inf_n([reply])
        nodes = extract_nodes(["a", "b", "c"], "file:///x.txt", inf)
        positions = [n["metadata"]["chunk_position"] for n in nodes]
        assert positions == [0, 1, 2]


# ── ReaderDevice format='nodes' ───────────────────────────────────────────────


class TestReaderDeviceNodes:
    def _make_device(self, node_reply: str = None) -> "ReaderDevice":
        from devices.reader.device import ReaderDevice

        if node_reply is None:
            node_reply = '{"nodes": [{"type": "factual", "narrative": "A fact.", "confidence": 0.8}]}'
        resp = MagicMock()
        resp.model = "qwen2.5:7b"
        resp.text = node_reply
        inf = MagicMock()
        inf.dispatch.return_value = resp
        return ReaderDevice(inference=inf)

    def test_file_uri_nodes_returns_list(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Some content here.", encoding="utf-8")
        device = self._make_device()
        result = device.read(f"file://{f}", format="nodes")
        assert isinstance(result, list)

    def test_nodes_have_required_keys(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Some interesting content.", encoding="utf-8")
        device = self._make_device()
        result = device.read(f"file://{f}", format="nodes")
        assert len(result) >= 1
        node = result[0]
        assert "narrative" in node
        assert "memory_type" in node
        assert "metadata" in node

    def test_binary_content_returns_empty_list(self, tmp_path):
        from devices.reader.device import ReaderDevice
        from devices.reader.uri import FetchResult

        device = self._make_device()
        with patch("devices.reader.device.fetch_uri") as mock_fetch:
            mock_fetch.return_value = FetchResult(
                uri="calibre://42",
                content="",
                sha256="def456",
                content_type="application/epub+zip",
                size_bytes=1024,
                from_cache=False,
                blob_path=tmp_path / "fake.epub.bin",
                fetched_at="2026-01-01T00:00:00+00:00",
            )
            result = device.read("calibre://42", format="nodes")
        assert result == []

    def test_summary_mode_still_works_after_node_mode_added(self, tmp_path):
        """Regression: adding format=nodes must not break format=summary."""
        f = tmp_path / "doc.txt"
        f.write_text("Content here.", encoding="utf-8")

        from devices.reader.device import ReaderDevice

        call_count = [0]

        def dispatch_side(req):
            resp = MagicMock()
            call_count[0] += 1
            resp.text = "exec summary" if call_count[0] == 1 else "detail text"
            return resp

        inf = MagicMock()
        inf.dispatch.side_effect = dispatch_side
        device = ReaderDevice(inference=inf)
        result = device.read(f"file://{f}", format="summary")
        assert "exec" in result
        assert "detail" in result
        assert "chunks" in result

    def test_unknown_format_still_raises_value_error(self, tmp_path):
        from devices.reader.device import ReaderDevice

        device = self._make_device()
        with pytest.raises(ValueError, match="Unknown format"):
            device.read("file:///x.txt", format="banana")
