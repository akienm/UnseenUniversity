"""
tests/test_run_tests_tool.py — Unit tests for ops.run_tests()
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _import_run_tests():
    """Import run_tests without triggering DB connections in ops.py module load."""
    import importlib
    import types

    # Save originals for all modules we may stub, so we can restore after
    # load. Missing devices.igor.tools.registry from this list bled a fake
    # _Registry into downstream tests that looked up tools at call-time
    # (test_tiered_research, test_tool_discovery_semantic).
    _stub_pkgs = [
        "unseen_university.devices.igor.tools",
        "unseen_university.devices.igor.tools.registry",
        "unseen_university.devices.igor.memory",
        "unseen_university.devices.igor.paths",
    ]
    _orig_mods = {pkg: sys.modules.get(pkg) for pkg in _stub_pkgs}

    # Build a minimal fake package tree so ops.py imports don't explode
    for pkg in _stub_pkgs:
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    # Stub registry so Tool/registry.register calls are no-ops
    fake_registry_mod = types.ModuleType("unseen_university.devices.igor.tools.registry")

    class _Tool:
        def __init__(self, **kwargs):
            pass

    class _Registry:
        def register(self, tool):
            pass

    fake_registry_mod.Tool = _Tool
    fake_registry_mod.registry = _Registry()
    sys.modules["unseen_university.devices.igor.tools.registry"] = fake_registry_mod

    # Stub paths (unconditional — ops.py needs a callable paths at import time)
    fake_paths_mod = types.ModuleType("unseen_university.devices.igor.paths")
    fake_paths_mod.paths = MagicMock()
    sys.modules["unseen_university.devices.igor.paths"] = fake_paths_mod

    # Stub psycopg2 so module-level DB code doesn't fail
    if "psycopg2" not in sys.modules:
        fake_pg = types.ModuleType("psycopg2")
        fake_pg.connect = MagicMock()
        sys.modules["psycopg2"] = fake_pg

    # Now import the module fresh (or reuse cached)
    mod_name = "unseen_university.devices.igor.tools.ops"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        mod_name,
        Path(__file__).parent.parent.parent / "devices" / "igor" / "tools" / "ops.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    # Restore all real modules so other test files aren't polluted by our stubs
    for pkg, orig in _orig_mods.items():
        if orig is not None:
            sys.modules[pkg] = orig
        else:
            sys.modules.pop(pkg, None)

    return mod.run_tests


def _get_run_tests():
    """Cache the imported function across tests."""
    if not hasattr(_get_run_tests, "_fn"):
        _get_run_tests._fn = _import_run_tests()
    return _get_run_tests._fn


def test_run_tests_returns_string():
    """run_tests() should always return a string."""
    run_tests = _get_run_tests()

    fake_result = MagicMock()
    fake_result.stdout = "1 passed\n"
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = run_tests()

    assert isinstance(result, str)
    assert len(result) > 0


def test_run_tests_truncates_long_output():
    """run_tests() should return only the last 30 lines when output exceeds 30 lines."""
    run_tests = _get_run_tests()

    lines = [f"line {i}" for i in range(50)]
    fake_result = MagicMock()
    fake_result.stdout = "\n".join(lines)
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = run_tests()

    returned_lines = result.splitlines()
    # First line is the [exit:N] prefix added by run_tests() for pe_chain's
    # pass/fail classification; remaining lines are the last 30 of output.
    assert returned_lines[0].startswith("[exit:")
    tail_lines = returned_lines[1:]
    assert len(tail_lines) == 30
    # Must be the LAST 30 lines
    assert tail_lines[0] == "line 20"
    assert tail_lines[-1] == "line 49"


def test_run_tests_handles_exception():
    """run_tests() should return an error string when subprocess raises."""
    run_tests = _get_run_tests()

    with patch("subprocess.run", side_effect=RuntimeError("pytest not found")):
        result = run_tests()

    assert isinstance(result, str)
    assert "[run_tests] error:" in result
    assert "pytest not found" in result


def test_run_tests_timeout_returns_distinct_marker():
    """TimeoutExpired must return '[run_tests] timeout' — distinct from
    '[run_tests] error' so pe_chain can classify preflight-timeout vs
    red-suite (T-pe-chain-preflight-timeout-misdiagnosis).
    """
    import subprocess

    run_tests = _get_run_tests()

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=300),
    ):
        result = run_tests()

    assert isinstance(result, str)
    assert "[run_tests] timeout" in result
    # Must NOT masquerade as a generic error — pe_chain branches on this
    assert "[run_tests] error" not in result
