"""
Tests for ReaderDevice summary mode (T-reader-summary-mode).

Inference is mocked — no LLM calls, no network. Tests verify:
  - exec/detail/chunks keys present and non-empty for text URIs
  - chunk boundaries respected
  - binary content (empty FetchResult.content) → empty result, no error
  - format='nodes' raises NotImplementedError (not yet shipped)
  - format=unknown raises ValueError
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.reader.chunker import chunk_text
from devices.reader.device import ReaderDevice, _llm_summarize, _summary_from_text

# ── chunk_text ────────────────────────────────────────────────────────────────


class TestChunkText:
    def test_single_short_paragraph_one_chunk(self):
        text = "Hello world. This is a paragraph."
        assert len(chunk_text(text, max_words=500)) == 1

    def test_splits_on_paragraph_boundary(self):
        para_a = " ".join(["word"] * 300)
        para_b = " ".join(["other"] * 300)
        text = f"{para_a}\n\n{para_b}"
        chunks = chunk_text(text, max_words=400)
        assert len(chunks) == 2
        assert "word" in chunks[0]
        assert "other" in chunks[1]

    def test_wall_of_text_splits_by_word_count(self):
        text = " ".join(["x"] * 1200)
        chunks = chunk_text(text, max_words=500)
        assert len(chunks) == 3
        assert all(len(c.split()) <= 500 for c in chunks)

    def test_empty_text_returns_single_chunk(self):
        result = chunk_text("")
        assert len(result) == 1

    def test_whitespace_only_returns_single_empty_chunk(self):
        result = chunk_text("   \n\n   ")
        assert result == [""]


# ── _llm_summarize (unit, inference mocked) ───────────────────────────────────


class TestLlmSummarize:
    def _mock_inference(self, reply: str) -> MagicMock:
        resp = MagicMock()
        resp.text = reply
        inf = MagicMock()
        inf.dispatch.return_value = resp
        return inf

    def test_exec_tier_returns_stripped_text(self):
        inf = self._mock_inference("  Summary sentence.  ")
        result = _llm_summarize("some content", "exec", inf)
        assert result == "Summary sentence."

    def test_detail_tier_returns_stripped_text(self):
        inf = self._mock_inference("  Detail paragraph.  ")
        result = _llm_summarize("some content", "detail", inf)
        assert result == "Detail paragraph."

    def test_long_content_truncated_before_dispatch(self):
        inf = self._mock_inference("ok")
        long_text = " ".join(["word"] * 4000)
        _llm_summarize(long_text, "exec", inf)
        call_args = inf.dispatch.call_args[0][0]
        content = call_args.messages[0]["content"]
        # Should contain truncation marker
        assert "[content truncated]" in content


# ── _summary_from_text ────────────────────────────────────────────────────────


class TestSummaryFromText:
    def _mock_inference(
        self, exec_reply: str = "exec summary", detail_reply: str = "detail para"
    ) -> MagicMock:
        call_count = [0]

        def dispatch_side(req):
            resp = MagicMock()
            call_count[0] += 1
            resp.text = exec_reply if call_count[0] == 1 else detail_reply
            return resp

        inf = MagicMock()
        inf.dispatch.side_effect = dispatch_side
        return inf

    def test_returns_all_three_keys(self):
        inf = self._mock_inference()
        result = _summary_from_text("Some content here.", inf)
        assert "exec" in result
        assert "detail" in result
        assert "chunks" in result

    def test_chunks_non_empty(self):
        inf = self._mock_inference()
        result = _summary_from_text("Paragraph one.\n\nParagraph two.", inf)
        assert len(result["chunks"]) >= 1

    def test_inference_failure_returns_empty_strings(self):
        inf = MagicMock()
        inf.dispatch.side_effect = RuntimeError("LLM down")
        result = _summary_from_text("Some text.", inf)
        assert result["exec"] == ""
        assert result["detail"] == ""
        assert len(result["chunks"]) >= 1  # chunks don't need inference


# ── ReaderDevice.read(format='summary') ───────────────────────────────────────


class TestReaderDeviceSummary:
    def _make_device(
        self, exec_text: str = "exec", detail_text: str = "detail"
    ) -> ReaderDevice:
        call_count = [0]

        def dispatch_side(req):
            resp = MagicMock()
            call_count[0] += 1
            resp.text = exec_text if call_count[0] == 1 else detail_text
            return resp

        inf = MagicMock()
        inf.dispatch.side_effect = dispatch_side
        return ReaderDevice(inference=inf)

    def _mock_http(self, body: bytes, content_type: str = "text/html"):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.headers.get = MagicMock(return_value=content_type)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_read_https_summary_has_all_keys(self, tmp_path):
        body = b"<html><body><p>Article content here.</p></body></html>"
        device = self._make_device("Short summary.", "Longer detail paragraph.")
        mock_resp = self._mock_http(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("devices.reader.device.fetch_uri") as mock_fetch:
                from devices.reader.uri import FetchResult

                mock_fetch.return_value = FetchResult(
                    uri="https://example.com/",
                    content="Article content here.",
                    sha256="abc123",
                    content_type="text/html",
                    size_bytes=len(body),
                    from_cache=False,
                    blob_path=tmp_path / "fake.blob.bin",
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
                result = device.read("https://example.com/", format="summary")

        assert "exec" in result
        assert "detail" in result
        assert "chunks" in result
        assert result["exec"] == "Short summary."
        assert result["detail"] == "Longer detail paragraph."
        assert len(result["chunks"]) >= 1

    def test_read_file_uri_summary(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
        device = self._make_device()
        result = device.read(f"file://{f}", format="summary")
        assert "exec" in result
        assert "chunks" in result
        assert len(result["chunks"]) >= 1

    def test_binary_content_returns_empty_result(self, tmp_path):
        """epub/pdf content is empty at this tier — no error, no crash."""
        device = self._make_device()
        with patch("devices.reader.device.fetch_uri") as mock_fetch:
            from devices.reader.uri import FetchResult

            mock_fetch.return_value = FetchResult(
                uri="calibre://42",
                content="",  # binary — empty text
                sha256="def456",
                content_type="application/epub+zip",
                size_bytes=1024,
                from_cache=False,
                blob_path=tmp_path / "fake.epub.bin",
                fetched_at="2026-01-01T00:00:00+00:00",
            )
            result = device.read("calibre://42", format="summary")

        assert result == {"exec": "", "detail": "", "chunks": []}

    def test_format_nodes_raises_not_implemented(self, tmp_path):
        device = self._make_device()
        with patch("devices.reader.device.fetch_uri") as mock_fetch:
            from devices.reader.uri import FetchResult

            mock_fetch.return_value = FetchResult(
                uri="file:///test.txt",
                content="some text",
                sha256="aaa",
                content_type="text/plain",
                size_bytes=9,
                from_cache=False,
                blob_path=None,
                fetched_at="2026-01-01T00:00:00+00:00",
            )
            with pytest.raises(NotImplementedError, match="T-reader-node-mode"):
                device.read("file:///test.txt", format="nodes")

    def test_unknown_format_raises_value_error(self, tmp_path):
        device = self._make_device()
        with pytest.raises(ValueError, match="Unknown format"):
            device.read("file:///x.txt", format="banana")

    def test_self_test_passes(self):
        device = ReaderDevice()
        result = device.self_test()
        assert result["passed"] is True
