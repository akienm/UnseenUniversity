"""Tests for devices/nanny/sweeps/code_sweep.py — AST code indexer."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.nanny.sweeps.code_sweep import (
    CodeSymbol,
    extract_symbols,
    iter_py_files,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return p


# ── extract_symbols ────────────────────────────────────────────────────────────

def test_extracts_top_level_function(tmp_path):
    f = _write(tmp_path, "foo.py", """
        def greet(name: str) -> str:
            \"\"\"Return a greeting.\"\"\"
            return f"Hello {name}"
    """)
    syms = extract_symbols(f, tmp_path)
    assert len(syms) == 1
    s = syms[0]
    assert s.symbol == "greet"
    assert s.kind == "function"
    assert "greet" in s.summary
    assert "greeting" in s.summary  # docstring line
    assert s.content_hash  # non-empty


def test_extracts_async_function(tmp_path):
    f = _write(tmp_path, "bar.py", """
        async def fetch(url: str) -> bytes:
            pass
    """)
    syms = extract_symbols(f, tmp_path)
    assert len(syms) == 1
    assert syms[0].kind == "async_function"


def test_extracts_class_and_methods(tmp_path):
    f = _write(tmp_path, "cls.py", """
        class Foo:
            \"\"\"A simple class.\"\"\"

            def bar(self) -> None:
                pass

            def baz(self, x: int) -> int:
                return x + 1
    """)
    syms = extract_symbols(f, tmp_path)
    names = [s.symbol for s in syms]
    kinds = {s.symbol: s.kind for s in syms}
    assert "Foo" in names
    assert "Foo.bar" in names
    assert "Foo.baz" in names
    assert kinds["Foo"] == "class"
    assert kinds["Foo.bar"] == "method"
    assert kinds["Foo.baz"] == "method"


def test_class_summary_includes_bases(tmp_path):
    f = _write(tmp_path, "sub.py", """
        class Child(Parent):
            pass
    """)
    syms = extract_symbols(f, tmp_path)
    assert any("Parent" in s.summary for s in syms if s.kind == "class")


def test_no_docstring_still_produces_symbol(tmp_path):
    f = _write(tmp_path, "nodoc.py", """
        def add(a, b):
            return a + b
    """)
    syms = extract_symbols(f, tmp_path)
    assert len(syms) == 1
    assert "add" in syms[0].summary


def test_syntax_error_returns_empty(tmp_path):
    f = _write(tmp_path, "bad.py", "def oops(:\n    pass\n")
    syms = extract_symbols(f, tmp_path)
    assert syms == []


def test_relative_path_uses_repo_root(tmp_path):
    sub = tmp_path / "devices" / "nanny"
    sub.mkdir(parents=True)
    f = sub / "device.py"
    f.write_text("def hello(): pass\n")
    syms = extract_symbols(f, tmp_path)
    assert syms[0].path == "devices/nanny/device.py"


def test_content_hash_changes_when_body_changes(tmp_path):
    f = _write(tmp_path, "v1.py", """
        def compute(x):
            return x * 2
    """)
    syms1 = extract_symbols(f, tmp_path)
    f.write_text("def compute(x):\n    return x * 3\n")
    syms2 = extract_symbols(f, tmp_path)
    assert syms1[0].content_hash != syms2[0].content_hash


def test_summary_truncated_to_500(tmp_path):
    long_doc = "x" * 600
    f = _write(tmp_path, "long.py", f'''
        def long_fn(a, b, c, d, e, f, g, h):
            """{long_doc}"""
            pass
    ''')
    syms = extract_symbols(f, tmp_path)
    assert len(syms[0].summary) <= 500


# ── iter_py_files ──────────────────────────────────────────────────────────────

def test_iter_py_files_walks_devices_and_uu(tmp_path):
    (tmp_path / "devices" / "foo").mkdir(parents=True)
    (tmp_path / "unseen_university").mkdir()
    (tmp_path / "devices" / "foo" / "bar.py").write_text("")
    (tmp_path / "unseen_university" / "baz.py").write_text("")
    (tmp_path / "other" / "skip.py").mkdir(parents=True)  # not walked

    files = list(iter_py_files(tmp_path))
    paths = [str(f) for f in files]
    assert any("bar.py" in p for p in paths)
    assert any("baz.py" in p for p in paths)


def test_iter_py_files_skips_pycache(tmp_path):
    cache = tmp_path / "devices" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "cached.py").write_text("")
    files = list(iter_py_files(tmp_path))
    assert not any("__pycache__" in str(f) for f in files)


# ── run_sweep dry_run ──────────────────────────────────────────────────────────

def test_run_sweep_dry_run_counts_symbols(tmp_path):
    (tmp_path / "devices").mkdir()
    f = tmp_path / "devices" / "sample.py"
    f.write_text("def foo(): pass\nclass Bar: pass\n")

    from devices.nanny.sweeps.code_sweep import run_sweep
    result = run_sweep(repo_root=tmp_path, dry_run=True)
    assert result["inserted"] >= 2
    assert result["errors"] == 0


# ── Nanny device integration ───────────────────────────────────────────────────

def test_nanny_default_schedule_includes_code_sweep():
    from devices.nanny.device import _DEFAULT_SCHEDULE
    ids = [e["entry_id"] for e in _DEFAULT_SCHEDULE]
    assert "nightly_code_sweep" in ids
    sweep_entry = next(e for e in _DEFAULT_SCHEDULE if e["entry_id"] == "nightly_code_sweep")
    assert sweep_entry["action_type"] == "run_code_sweep"


def test_nanny_fire_entry_handles_run_code_sweep():
    from devices.nanny.device import NannyOggDevice, ScheduleEntry

    device = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="test_sweep",
        condition_type="cron",
        condition_params={},
        action_type="run_code_sweep",
        action_params={},
    )

    sweep_called = []

    def _fake_sweep(*args, **kwargs):
        sweep_called.append(True)
        return {"inserted": 1, "updated": 0, "unchanged": 0, "errors": 0}

    with patch("devices.nanny.sweeps.code_sweep.run_sweep", _fake_sweep):
        with patch.object(device, "_post_to_channel"):
            ok = device.fire_entry(entry)

    assert ok is True
    assert sweep_called
