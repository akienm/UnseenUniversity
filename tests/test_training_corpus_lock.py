"""test_training_corpus_lock.py — T-cc-walk-16

Verifies that concurrent _save_index() calls produce a valid, uncorrupted
index.json rather than a partial write.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_corpus(tmp_path, monkeypatch):
    """Redirect CORPUS_DIR and INDEX_FILE to a temp directory."""
    import wild_igor.igor.cognition.training_corpus as tc

    monkeypatch.setattr(tc, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(tc, "INDEX_FILE", tmp_path / "index.json")
    return tmp_path


class TestSaveIndexConcurrency:
    def test_concurrent_writes_produce_valid_json(self, tmp_corpus):
        """Two threads calling _save_index() concurrently must not corrupt the file."""
        from wild_igor.igor.cognition.training_corpus import _save_index

        errors: list[Exception] = []

        def writer(entry: dict) -> None:
            try:
                _save_index(entry)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=writer, args=({"book_1": {"status": "pending"}},))
        t2 = threading.Thread(target=writer, args=({"book_2": {"status": "complete"}},))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Unexpected errors during concurrent writes: {errors}"
        index_path = tmp_corpus / "index.json"
        assert index_path.exists(), "index.json not written"
        parsed = json.loads(index_path.read_text())
        assert isinstance(parsed, dict), "index.json is not a valid JSON object"

    def test_load_after_concurrent_writes_is_valid(self, tmp_corpus):
        """_load_index() after concurrent writes must return a dict (not raise)."""
        from wild_igor.igor.cognition.training_corpus import _load_index, _save_index

        def writer(entry: dict) -> None:
            for _ in range(5):
                _save_index(entry)

        t1 = threading.Thread(target=writer, args=({"a": {}},))
        t2 = threading.Thread(target=writer, args=({"b": {}},))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        result = _load_index()
        assert isinstance(result, dict)
