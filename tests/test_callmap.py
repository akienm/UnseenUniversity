"""tests for lab/claudecode/callmap.py — T-callmap-tool.

Covers API discovery (default, __api__ override, # API: comment), direct-call
caller detection (intra-file, cross-file imports), subprocess invocation
detection, NOISE_NAMES filter, idempotency of the rendered output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.claudecode.callmap import (
    NOISE_NAMES,
    attach_callers,
    discover_apis,
    render_markdown,
)


@pytest.fixture
def fixture_repo(tmp_path):
    """Build a tiny 3-file repo for callmap tests."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    # API module: two public functions, one private, one noise (main)
    (src / "api_mod.py").write_text('''"""Test API module."""

# API: Load the thing
def load_thing():
    return 42

def save_thing(x):
    return x

def _private():
    return 0

def main():
    pass

class Helper:
    def help_method(self):
        return "ok"

    def _private_method(self):
        return "no"
''')
    # Caller module: imports api_mod and uses both functions
    (src / "caller.py").write_text("""from src.api_mod import load_thing, save_thing
import src.api_mod as api

def use_things():
    a = load_thing()
    b = save_thing(a)
    c = api.load_thing()
    return c
""")
    # Subprocess caller: invokes api_mod.py via subprocess
    (src / "subproc_caller.py").write_text("""import subprocess
import sys

def run_via_subprocess():
    subprocess.run([sys.executable, 'api_mod.py', 'arg'])
""")
    # __api__ override module — only one function exposed
    (src / "narrow_mod.py").write_text("""__api__ = ['exposed_one']

def exposed_one():
    return 1

def hidden_two():
    return 2
""")
    # File that mentions API names only inside string literals or comments
    # — should not become a caller (currently substring prefilter would let
    # it through, but AST walk wouldn't find the call). Tests the prefilter
    # doesn't cause false positives.
    (src / "no_actual_call.py").write_text(
        """# load_thing is mentioned in this comment but never called.
DOC = "load_thing in a docstring, not a call."
"""
    )
    return tmp_path


# ── Discovery ─────────────────────────────────────────────────────────────


class TestDiscoverApis:
    def test_picks_up_default_public_functions(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        names = {a.name for a in apis}
        assert "load_thing" in names
        assert "save_thing" in names
        assert "Helper.help_method" in names

    def test_skips_private_and_noise(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        names = {a.name for a in apis}
        assert "_private" not in names
        assert "main" not in names  # NOISE_NAMES
        assert "Helper._private_method" not in names

    def test_api_comment_attaches_description(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        load = next(a for a in apis if a.name == "load_thing")
        assert load.description == "Load the thing"

    def test_explicit_api_list_narrows(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        names = {a.name for a in apis if "narrow_mod" in a.module}
        assert names == {"exposed_one"}, f"got {names}"


# ── Caller detection ───────────────────────────────────────────────────────


class TestAttachCallers:
    def test_direct_call_detected(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        load = next(a for a in apis if a.name == "load_thing")
        # caller.py uses load_thing twice (bare + dotted)
        caller_files = {str(c[0]) for c in load.callers}
        assert any("caller.py" in f for f in caller_files)

    def test_subprocess_invocation_detected(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        load = next(a for a in apis if a.name == "load_thing")
        # subproc_caller.py invokes api_mod.py via subprocess.run — every
        # API in api_mod should pick up that subprocess caller
        kinds = {c[2] for c in load.callers}
        assert "subprocess" in kinds, f"got kinds={kinds}"

    def test_string_mention_is_not_a_caller(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        load = next(a for a in apis if a.name == "load_thing")
        # no_actual_call.py mentions load_thing in a comment+string but
        # never actually calls it — must not appear as a caller
        caller_files = [str(c[0]) for c in load.callers]
        assert not any(
            "no_actual_call.py" in f for f in caller_files
        ), f"false positive caller: {caller_files}"

    def test_def_line_itself_not_recorded(self, fixture_repo):
        """The def line of the API itself shouldn't register as a self-caller."""
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        load = next(a for a in apis if a.name == "load_thing")
        # Filter to callers that are in api_mod.py at the def's lineno
        def_line_callers = [
            c for c in load.callers if "api_mod.py" in str(c[0]) and c[1] == load.lineno
        ]
        assert def_line_callers == []


# ── Output ────────────────────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_render_includes_module_and_api_headers(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        out = render_markdown(apis)
        assert "## `" in out  # module header
        assert "### `load_thing`" in out
        # description should appear when present
        assert "Load the thing" in out

    def test_render_idempotent(self, fixture_repo):
        """Re-rendering with the same input produces identical output."""
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        a = render_markdown(apis)
        # Re-discover + re-attach, render again
        apis2 = discover_apis([fixture_repo / "src"])
        attach_callers(apis2, [fixture_repo / "src"])
        b = render_markdown(apis2)
        assert a == b

    def test_no_callers_renders_explicitly(self, fixture_repo):
        apis = discover_apis([fixture_repo / "src"])
        attach_callers(apis, [fixture_repo / "src"])
        out = render_markdown(apis)
        # save_thing is called in caller.py, but Helper.help_method isn't
        # called anywhere — must show "(none found)"
        assert "_(none found)_" in out


# ── Constants ──────────────────────────────────────────────────────────────


class TestNoiseList:
    def test_main_is_noise(self):
        assert "main" in NOISE_NAMES

    def test_dunder_init_is_noise(self):
        assert "__init__" in NOISE_NAMES
