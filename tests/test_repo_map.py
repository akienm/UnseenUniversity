"""Tests for lab.claudecode.repo_map symbol extractor."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

import devlab.claudecode.repo_map as rm

SAMPLE_PY = textwrap.dedent("""\
    \"\"\"Sample module — testing repo_map.\"\"\"


    class Foo:
        \"\"\"A foo class.\"\"\"

        def __init__(self, x, y):
            self.x = x

        def method_a(self):
            pass

        @staticmethod
        def static_b(*args):
            pass


    class Bar:
        pass


    def top_level_fn(a, b, **kw):
        pass
    """)


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_PY)
    return f


def test_parse_file_module_doc(sample_file):
    result = rm.parse_file(sample_file)
    assert result["doc"] == "Sample module — testing repo_map."


def test_parse_file_classes(sample_file):
    result = rm.parse_file(sample_file)
    names = [c["name"] for c in result["classes"]]
    assert "Foo" in names
    assert "Bar" in names


def test_parse_file_class_doc(sample_file):
    result = rm.parse_file(sample_file)
    foo = next(c for c in result["classes"] if c["name"] == "Foo")
    assert foo["doc"] == "A foo class."


def test_parse_file_methods(sample_file):
    result = rm.parse_file(sample_file)
    foo = next(c for c in result["classes"] if c["name"] == "Foo")
    assert "__init__(self, x, y)" in foo["methods"]
    assert "method_a(self)" in foo["methods"]
    assert "static_b(*args)" in foo["methods"]


def test_parse_file_top_level_functions(sample_file):
    result = rm.parse_file(sample_file)
    assert "top_level_fn(a, b, **kw)" in result["functions"]


def test_parse_file_syntax_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def foo(\n  this is broken")
    result = rm.parse_file(bad)
    assert "error" in result
    assert "SyntaxError" in result["error"]


def test_collect_files_py_only(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.txt").write_text("not python")
    files = list(rm.collect_files([tmp_path]))
    names = {f.name for f in files}
    assert "a.py" in names
    assert "b.txt" not in names


def test_collect_files_recurses(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("y = 2")
    files = list(rm.collect_files([tmp_path]))
    assert any(f.name == "c.py" for f in files)


def test_collect_files_excludes_pycache(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("x = 1")
    files = list(rm.collect_files([tmp_path]))
    assert all("__pycache__" not in str(f) for f in files)


def test_collect_files_single_file(sample_file):
    files = list(rm.collect_files([sample_file]))
    assert len(files) == 1
    assert files[0] == sample_file


def test_render_text_contains_symbols(sample_file):
    entries = [rm.parse_file(sample_file)]
    text = rm.render_text(entries, root=sample_file.parent)
    assert "class Foo" in text
    assert "top_level_fn" in text
    assert "Sample module" in text


def test_render_text_method_truncation(tmp_path):
    many_methods = "\n".join(f"    def m{i}(self): pass" for i in range(20))
    src = f"class Big:\n{many_methods}\n"
    f = tmp_path / "big.py"
    f.write_text(src)
    text = rm.render_text([rm.parse_file(f)], root=tmp_path)
    assert "more)" in text


def test_render_text_relpath(sample_file):
    entries = [rm.parse_file(sample_file)]
    text = rm.render_text(entries, root=sample_file.parent)
    assert "sample.py" in text
    assert str(sample_file.parent) not in text


def test_build_map_integration(tmp_path):
    (tmp_path / "a.py").write_text("class A: pass")
    (tmp_path / "b.py").write_text("def b(): pass")
    entries = rm.build_map([tmp_path])
    assert len(entries) == 2
    names = {c["name"] for e in entries for c in e.get("classes", [])}
    assert "A" in names
    fns = {fn for e in entries for fn in e.get("functions", [])}
    assert "b()" in fns
