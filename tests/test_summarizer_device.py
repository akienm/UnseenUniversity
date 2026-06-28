"""
Tests for SummarizerDevice — tiered output (exec/detail/chunks).

Inference is mocked: these tests verify the routing, chunking logic, HTML
stripping, and format selection without hitting a real LLM or network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.summarizer.device import (
    SummarizerDevice,
    _chunk_text,
    _fetch_url,
    _strip_html,
    _make_app,
)

# ── Text chunking ─────────────────────────────────────────────────────────────


class TestChunkText:
    def test_single_short_paragraph_is_one_chunk(self):
        text = "Hello world. This is a short paragraph."
        chunks = _chunk_text(text, max_words=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_splits_on_paragraph_boundary(self):
        para_a = " ".join(["word"] * 300)
        para_b = " ".join(["other"] * 300)
        text = f"{para_a}\n\n{para_b}"
        chunks = _chunk_text(text, max_words=400)
        assert len(chunks) == 2
        assert "word" in chunks[0]
        assert "other" in chunks[1]

    def test_wall_of_text_splits_by_word_count(self):
        text = " ".join(["x"] * 1200)
        chunks = _chunk_text(text, max_words=500)
        assert len(chunks) == 3
        assert all(len(c.split()) <= 500 for c in chunks)

    def test_empty_paragraphs_ignored(self):
        text = "First para.\n\n\n\nSecond para."
        chunks = _chunk_text(text, max_words=500)
        assert len(chunks) == 1

    def test_never_returns_empty_list(self):
        assert _chunk_text("") != []
        assert _chunk_text("   ") != []


# ── HTML stripping ────────────────────────────────────────────────────────────


class TestStripHtml:
    def test_strips_tags(self):
        html = b"<html><body><p>Hello <b>world</b></p></body></html>"
        assert "Hello" in _strip_html(html)
        assert "<" not in _strip_html(html)

    def test_skips_script_content(self):
        html = b"<p>Good</p><script>evil()</script><p>Also good</p>"
        text = _strip_html(html)
        assert "Good" in text
        assert "evil" not in text

    def test_skips_style_content(self):
        html = b"<p>Content</p><style>.foo{color:red}</style>"
        assert "color" not in _strip_html(html)

    def test_handles_malformed_html(self):
        html = b"<p>Text without closing tag"
        assert "Text" in _strip_html(html)


# ── Summarizer device — format routing ───────────────────────────────────────


def _fake_inference(text="exec summary"):
    mock = MagicMock()
    mock.dispatch.return_value = MagicMock(text=text)
    return mock


class TestSummarizerDevice:
    def test_chunks_format_no_llm(self):
        device = SummarizerDevice(inference=MagicMock())
        result = device.summarize(content="para one\n\npara two", format="chunks")
        assert "chunks" in result
        assert "exec" not in result
        assert "detail" not in result
        assert isinstance(result["chunks"], list)

    def test_exec_format_calls_inference_once(self):
        inf = _fake_inference(text="short summary")
        device = SummarizerDevice(inference=inf)
        result = device.summarize(content="some content", format="exec")
        assert result["exec"] == "short summary"
        assert "detail" not in result
        assert "chunks" not in result
        assert inf.dispatch.call_count == 1

    def test_detail_format_calls_inference_once(self):
        inf = _fake_inference(text="long summary")
        device = SummarizerDevice(inference=inf)
        result = device.summarize(content="some content", format="detail")
        assert result["detail"] == "long summary"
        assert "exec" not in result
        assert inf.dispatch.call_count == 1

    def test_all_format_returns_three_tiers(self):
        mock_inf = MagicMock()
        mock_inf.dispatch.side_effect = [
            MagicMock(text="exec result"),
            MagicMock(text="detail result"),
        ]
        device = SummarizerDevice(inference=mock_inf)
        result = device.summarize(content="content here", format="all")
        assert "exec" in result
        assert "detail" in result
        assert "chunks" in result
        assert result["exec"] == "exec result"
        assert result["detail"] == "detail result"

    def test_raises_when_no_url_or_content(self):
        device = SummarizerDevice()
        with pytest.raises(ValueError, match="provide url or content"):
            device.summarize()

    def test_inference_failure_returns_empty_string_not_raise(self):
        inf = MagicMock()
        inf.dispatch.side_effect = RuntimeError("LLM down")
        device = SummarizerDevice(inference=inf)
        result = device.summarize(content="text", format="exec")
        assert result["exec"] == ""

    def test_url_fetch_and_summarize(self):
        inf = _fake_inference(text="page summary")
        device = SummarizerDevice(inference=inf)
        fake_response = MagicMock()
        fake_response.read.return_value = b"<p>Article content</p>"
        fake_response.headers = {"Content-Type": "text/html"}
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=fake_response):
            result = device.summarize(url="http://example.com", format="exec")
        assert result["exec"] == "page summary"

    def test_content_takes_precedence_over_url_when_both_given(self):
        inf = _fake_inference(text="content wins")
        device = SummarizerDevice(inference=inf)
        result = device.summarize(
            url="http://example.com",
            content="explicit content",
            format="exec",
        )
        assert result["exec"] == "content wins"
        # URL should not be fetched when content is provided
        inf.dispatch.assert_called_once()


# ── HTTP app ──────────────────────────────────────────────────────────────────


class TestSummarizerHttpApp:
    def test_post_summarize_chunks_only(self):
        from starlette.testclient import TestClient

        device = SummarizerDevice(inference=MagicMock())
        app = _make_app(device)
        with TestClient(app) as client:
            resp = client.post(
                "/api/summarize?format=chunks",
                json={"content": "para one\n\npara two"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "chunks" in data
        assert isinstance(data["chunks"], list)

    def test_post_summarize_exec(self):
        from starlette.testclient import TestClient

        mock_inf = MagicMock()
        mock_inf.dispatch.return_value = MagicMock(text="the summary")
        device = SummarizerDevice(inference=mock_inf)
        app = _make_app(device)
        with TestClient(app) as client:
            resp = client.post(
                "/api/summarize",
                json={"content": "some content", "format": "exec"},
            )
        assert resp.status_code == 200
        assert resp.json()["exec"] == "the summary"

    def test_post_missing_body_returns_400(self):
        from starlette.testclient import TestClient

        device = SummarizerDevice()
        app = _make_app(device)
        with TestClient(app) as client:
            resp = client.post("/api/summarize", json={})
        assert resp.status_code == 400

    def test_get_health(self):
        from starlette.testclient import TestClient

        inf = MagicMock()
        inf.health.return_value = {"status": "healthy"}
        device = SummarizerDevice(inference=inf)
        app = _make_app(device)
        with TestClient(app) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "status" in resp.json()
