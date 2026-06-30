"""
Grep-gate test: orphaned reasoner class bodies must be absent from
unseen_university/devices/igor, and module-level survivors must remain importable.

T-igor-delete-orphaned-reasoner-classes
"""

from __future__ import annotations

import re
from pathlib import Path

from unseen_university._uu_root import uu_root

IGOR = Path(uu_root()) / "unseen_university" / "devices" / "igor"


def _class_defs(class_name: str) -> list[str]:
    """Return list of 'file:lineno' for class definitions matching class_name."""
    pattern = re.compile(rf"^class {re.escape(class_name)}\b", re.MULTILINE)
    hits = []
    for f in IGOR.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            hits.append(f"{f.relative_to(IGOR)}:{lineno}")
    return hits


def test_ollama_reasoner_class_gone():
    hits = _class_defs("OllamaReasoner")
    assert not hits, f"class OllamaReasoner still defined at: {hits}"


def test_openrouter_reasoner_class_gone():
    hits = _class_defs("OpenRouterReasoner")
    assert not hits, f"class OpenRouterReasoner still defined at: {hits}"


def test_base_reasoner_class_gone():
    hits = _class_defs("BaseReasoner")
    assert not hits, f"class BaseReasoner still defined at: {hits}"


def test_local_reasoner_class_gone():
    hits = _class_defs("LocalReasoner")
    assert not hits, f"class LocalReasoner still defined at: {hits}"


def test_api_reasoner_class_gone():
    hits = _class_defs("APIReasoner")
    assert not hits, f"class APIReasoner still defined at: {hits}"


def test_browser_reasoner_class_gone():
    hits = _class_defs("BrowserReasoner")
    assert not hits, f"class BrowserReasoner still defined at: {hits}"


def test_module_level_survivors_importable():
    from unseen_university.devices.igor.cognition.reasoners.openrouter_reasoner import (
        MODEL_ALIASES,
    )
    from unseen_university.devices.igor.cognition.reasoners.ollama_reasoner import (
        OLLAMA_HOST,
        is_healthy,
        parse_preparse_csb,
    )
    assert MODEL_ALIASES is not None
    assert OLLAMA_HOST is not None
    assert callable(is_healthy)
    assert callable(parse_preparse_csb)


def test_igor_main_importable():
    import unseen_university.devices.igor.main  # noqa: F401
