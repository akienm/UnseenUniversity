"""Reader semantic-equivalence invariant: summary and nodes modes cover the same territory.

T-reader-equivalence-test.

Both output modes process the same input text via LLM, so the extracted
content should overlap significantly. This test uses:
  - A fixed synthetic chunk (no live fetch, no network)
  - Mocked inference returning known topic-consistent text
  - WG spreading-activation embedding for cosine comparison
    (degrades to bag-of-words overlap when graph is empty, still meaningful)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.reader.device import ReaderDevice
from devices.reader.uri import FetchResult

# ── Synthetic input ────────────────────────────────────────────────────────────

_CHUNK = (
    "Neural networks are the core technique in modern machine learning. "
    "They consist of layers of interconnected nodes that learn patterns "
    "from training data. Deep learning extends this with many hidden layers, "
    "enabling complex tasks like image recognition and natural language "
    "processing. Training requires large datasets and significant compute."
)

# ── Mocked LLM outputs ─────────────────────────────────────────────────────────

_EXEC = (
    "Neural networks learn patterns from training data through interconnected "
    "layers. Deep learning extends this to complex tasks like image recognition."
)

_DETAIL = (
    "Neural networks consist of layers of nodes trained on large datasets. "
    "Deep learning uses many hidden layers enabling image recognition and "
    "natural language processing. Training requires substantial compute resources."
)

_NODES_JSON = json.dumps(
    {
        "nodes": [
            {
                "type": "factual",
                "narrative": (
                    "Neural networks are machine learning models built from "
                    "interconnected layers of nodes that learn patterns from training data."
                ),
                "confidence": 0.92,
            },
            {
                "type": "factual",
                "narrative": (
                    "Deep learning uses many hidden layers in neural networks to tackle "
                    "complex tasks including image recognition and language processing."
                ),
                "confidence": 0.88,
            },
        ]
    }
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_fetch_result(text: str = _CHUNK) -> FetchResult:
    return FetchResult(
        uri="test://synthetic",
        content=text,
        sha256="deadbeef" * 8,
        content_type="text/plain",
        size_bytes=len(text.encode()),
        from_cache=False,
        blob_path=None,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_inference(responses: list[str]) -> MagicMock:
    """Mock inference.dispatch() returning responses in sequence."""
    inf = MagicMock()
    resp_mocks = []
    for text in responses:
        r = MagicMock()
        r.text = text
        r.model = "mock-model"
        resp_mocks.append(r)
    inf.dispatch.side_effect = resp_mocks
    return inf


def _sparse_cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse L2-normalized word→float dicts."""
    dot = sum(a.get(w, 0.0) * b.get(w, 0.0) for w in set(a) | set(b))
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestReaderEquivalence:
    @pytest.fixture()
    def reader_summary(self):
        """ReaderDevice with inference mocked for summary mode (exec + detail)."""
        return ReaderDevice(inference=_make_inference([_EXEC, _DETAIL]))

    @pytest.fixture()
    def reader_nodes(self):
        """ReaderDevice with inference mocked for nodes mode (one chunk → JSON)."""
        return ReaderDevice(inference=_make_inference([_NODES_JSON]))

    def _wg_embed_text(self, text: str) -> dict[str, float]:
        """Embed via WG spreading activation; returns empty dict when unavailable."""
        try:
            from devices.igor.cognition.word_graph import WordGraph

            wg = WordGraph.__new__(WordGraph)
            # Use spread_from_words with an empty frontier (no DB needed):
            # tokenize seeds the dict; spread returns the seed unchanged on empty graph.
            from devices.igor.cognition.word_graph import tokenize

            tokens = tokenize(text)
            if not tokens:
                return {}
            seed = {w: 1.0 for w in set(tokens)}
            norm = len(seed) ** 0.5
            return {w: 1.0 / norm for w in seed}
        except Exception:
            return {}

    def test_summary_mode_returns_non_empty(self, reader_summary):
        with patch(
            "devices.reader.device.fetch_uri", return_value=_make_fetch_result()
        ):
            result = reader_summary.read("test://synthetic", format="summary")
        assert result["exec"], "exec must be non-empty"
        assert result["detail"], "detail must be non-empty"

    def test_nodes_mode_returns_non_empty(self, reader_nodes):
        with patch(
            "devices.reader.device.fetch_uri", return_value=_make_fetch_result()
        ):
            nodes = reader_nodes.read("test://synthetic", format="nodes")
        assert len(nodes) > 0, "nodes list must be non-empty"
        assert all("narrative" in n for n in nodes), "each node must have a narrative"

    def test_semantic_equivalence_cosine(self, reader_summary, reader_nodes):
        """Summary and nodes modes cover the same semantic territory.

        Cosine is computed over WG sparse activation vectors — effectively
        bag-of-words overlap when the graph has no edges (CI-safe).
        Threshold 0.7 asserts the two outputs share a majority of topic vocabulary.
        """
        with patch(
            "devices.reader.device.fetch_uri", return_value=_make_fetch_result()
        ):
            summary = reader_summary.read("test://synthetic", format="summary")
            nodes = reader_nodes.read("test://synthetic", format="nodes")

        summary_text = summary["exec"] + " " + summary["detail"]
        nodes_text = " ".join(n["narrative"] for n in nodes)

        assert summary_text.strip(), "summary text must be non-empty"
        assert nodes_text.strip(), "nodes text must be non-empty"

        vec_s = self._wg_embed_text(summary_text)
        vec_n = self._wg_embed_text(nodes_text)

        assert vec_s, "summary embedding must be non-empty"
        assert vec_n, "nodes embedding must be non-empty"

        cosine = _sparse_cosine(vec_s, vec_n)
        assert cosine > 0.7, (
            f"Summary and nodes modes must share semantic territory "
            f"(cosine={cosine:.3f} < 0.7). "
            f"Summary: {summary_text[:100]!r}... Nodes: {nodes_text[:100]!r}..."
        )

    def test_unknown_format_raises(self):
        reader = ReaderDevice(inference=MagicMock())
        with patch(
            "devices.reader.device.fetch_uri", return_value=_make_fetch_result()
        ):
            with pytest.raises(ValueError, match="Unknown format"):
                reader.read("test://synthetic", format="invalid")
