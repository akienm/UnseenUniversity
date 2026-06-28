"""T-local-preparse-fallback: local-only mini-LLM preparse fallback.

Tests MUST NOT invoke cloud LLMs. Ollama calls are patched to return
canned responses so the suite runs offline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.local_preparse import (
    LocalPreparser,
    preparse_local,
    default_preparser,
    set_default_preparser,
)

_FAKE_GOOD_RESPONSE = """[PARSED_INPUT]
intent: explanation_request
tone: curious
complexity: medium
entities: none
requires_tools: false
memory_hints: reasoning, pipeline
should_escalate: false
"""


def setup_function(_fn):
    """Reset module-level default preparser between tests."""
    set_default_preparser(None)


def test_empty_input_returns_none():
    p = LocalPreparser(model="test", timeout_sec=1.0)
    assert p.preparse("") is None
    assert p.preparse("   ") is None
    assert p.preparse(None) is None  # type: ignore


def test_disabled_returns_none(monkeypatch):
    """IGOR_LOCAL_PREPARSE_ENABLED=false → no-op (even with valid input)."""
    monkeypatch.setenv("IGOR_LOCAL_PREPARSE_ENABLED", "false")
    p = LocalPreparser(model="test")
    assert p.preparse("explain the reasoning pipeline") is None


def test_enabled_by_default(monkeypatch):
    """IGOR_LOCAL_PREPARSE_ENABLED unset → enabled (local-first default)."""
    monkeypatch.delenv("IGOR_LOCAL_PREPARSE_ENABLED", raising=False)
    # We don't actually call Ollama here — just check the enabled gate
    # allows invocation to proceed.
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value=_FAKE_GOOD_RESPONSE):
        result = p.preparse("some input")
    assert result is not None
    assert "[PARSED_INPUT]" in result


def test_success_returns_csb_block():
    """Successful Ollama response → returns the CSB block."""
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value=_FAKE_GOOD_RESPONSE):
        result = p.preparse("explain the reasoning pipeline")
    assert result is not None
    assert "[PARSED_INPUT]" in result
    assert "explanation_request" in result


def test_missing_sentinel_returns_none():
    """Ollama response without [PARSED_INPUT] marker → None."""
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value="random garbage text"):
        result = p.preparse("explain X")
    assert result is None
    assert p.failure_count == 1


def test_ollama_exception_returns_none():
    """Ollama client raising → None, failure counted, no propagation."""
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", side_effect=RuntimeError("ollama down")):
        result = p.preparse("explain X")
    assert result is None
    assert p.failure_count == 1


def test_ollama_returns_none_counted_as_failure():
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value=None):
        result = p.preparse("explain X")
    assert result is None
    assert p.failure_count == 1


def test_latency_is_tracked():
    """last_latency_ms populated after each invocation."""
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value=_FAKE_GOOD_RESPONSE):
        p.preparse("explain X")
    assert p.last_latency_ms is not None
    assert p.last_latency_ms >= 0


def test_invocation_count_increments():
    p = LocalPreparser(model="test")
    with patch.object(p, "_call_ollama", return_value=_FAKE_GOOD_RESPONSE):
        p.preparse("first")
        p.preparse("second")
        p.preparse("third")
    assert p.invocation_count == 3


def test_disabled_does_not_increment_invocation(monkeypatch):
    monkeypatch.setenv("IGOR_LOCAL_PREPARSE_ENABLED", "false")
    p = LocalPreparser(model="test")
    p.preparse("anything")
    assert p.invocation_count == 0


def test_input_truncation_to_300_chars():
    """Long input is truncated before sending to Ollama — local small
    models work best with tight prompts."""
    p = LocalPreparser(model="test")
    long_input = "word " * 200  # ~1000 chars
    with patch.object(p, "_call_ollama", return_value=_FAKE_GOOD_RESPONSE) as mock_call:
        p.preparse(long_input)
    # Extract the prompt passed to ollama
    called_prompt = mock_call.call_args[0][0]
    # The truncated input segment must be <= 300 chars + prompt overhead
    assert len(called_prompt) < 1500  # prompt template is ~500 chars


def test_default_preparser_is_singleton():
    """default_preparser() returns the same instance across calls."""
    a = default_preparser()
    b = default_preparser()
    assert a is b


def test_preparse_local_convenience():
    """preparse_local() routes to the default preparser."""
    mock_preparser = MagicMock()
    mock_preparser.preparse.return_value = _FAKE_GOOD_RESPONSE
    set_default_preparser(mock_preparser)

    result = preparse_local("hello")
    assert result == _FAKE_GOOD_RESPONSE
    mock_preparser.preparse.assert_called_once_with("hello")


def test_custom_model_and_timeout():
    """Constructor parameters override defaults."""
    p = LocalPreparser(model="custom-model", timeout_sec=0.5, host="http://test:11434")
    assert p.model == "custom-model"
    assert p.timeout_sec == 0.5
    assert p.host == "http://test:11434"


def test_never_calls_cloud_by_construction():
    """Hard invariant of this ticket: local-only. The module must not
    import anthropic/openai/openrouter or reference cloud API hosts
    anywhere in CODE (docstrings may mention them to enforce the rule)."""
    import unseen_university.devices.igor.cognition.local_preparse as mod
    import inspect

    source = inspect.getsource(mod)
    # Strip docstrings via AST walk — keep code-only text
    import ast

    tree = ast.parse(source)
    # Collect docstring ranges; anything inside a string literal at
    # statement level (module docstring, class docstring, func docstring)
    # is excluded.
    code_lines = source.splitlines()
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            ds = ast.get_docstring(node, clean=False)
            if ds:
                # The docstring Expr spans lineno..end_lineno on the first
                # stmt. Mark those lines excluded.
                first_stmt = node.body[0] if node.body else None
                if first_stmt and isinstance(first_stmt, ast.Expr):
                    for ln in range(
                        first_stmt.lineno,
                        (first_stmt.end_lineno or first_stmt.lineno) + 1,
                    ):
                        docstring_lines.add(ln)

    code_only = "\n".join(
        ln for i, ln in enumerate(code_lines, start=1) if i not in docstring_lines
    ).lower()
    assert "openrouter" not in code_only
    assert "anthropic" not in code_only
    assert "api.openai" not in code_only


def test_module_level_exports():
    """Sanity: public API is accessible as documented in docstring."""
    from unseen_university.devices.igor.cognition import local_preparse

    assert hasattr(local_preparse, "LocalPreparser")
    assert hasattr(local_preparse, "preparse_local")
    assert hasattr(local_preparse, "default_preparser")
    assert hasattr(local_preparse, "set_default_preparser")
