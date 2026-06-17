"""
test_wiring_check.py — T-audit-wiring-check

Tests for the wiring check script that verifies gated features
have end-to-end wiring before enabling.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.wiring_check import (
    _check_stubs_near_gate,
    _find_enabled_switches,
    _find_references,
    _load_switches,
    run_wiring_check,
)


class TestLoadSwitches:
    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            f.write("IGOR_FOO=true\nIGOR_BAR=false\n# comment\n")
            f.flush()
            switches = _load_switches(Path(f.name))
        assert switches == {"IGOR_FOO": "true", "IGOR_BAR": "false"}

    def test_load_missing_file(self):
        switches = _load_switches(Path("/nonexistent/file.cfg"))
        assert switches == {}

    def test_load_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            f.write("")
            f.flush()
            switches = _load_switches(Path(f.name))
        assert switches == {}


class TestFindEnabled:
    def test_finds_true(self):
        switches = {"A": "true", "B": "false", "C": "1", "D": "yes"}
        enabled = _find_enabled_switches(switches)
        assert enabled == ["A", "C", "D"]

    def test_case_insensitive(self):
        switches = {"A": "True", "B": "TRUE", "C": "False"}
        enabled = _find_enabled_switches(switches)
        assert enabled == ["A", "B"]

    def test_empty(self):
        assert _find_enabled_switches({}) == []


class TestFindReferences:
    def test_finds_in_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.py").write_text(
                'if os.getenv("IGOR_FOO", "false") == "true":\n    do_stuff()\n'
            )
            refs = _find_references("IGOR_FOO", Path(tmpdir))
            assert len(refs) == 1

    def test_no_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.py").write_text("x = 1\n")
            refs = _find_references("IGOR_NONEXISTENT", Path(tmpdir))
            assert refs == []


class TestCheckStubs:
    def test_detects_todo(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                'if os.getenv("IGOR_FOO") == "true":\n'
                "    # TODO: implement this\n"
                "    pass\n"
            )
            f.flush()
            issues = _check_stubs_near_gate("IGOR_FOO", f.name)
        assert len(issues) >= 1
        assert any("TODO" in i for i in issues)

    def test_detects_not_implemented(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                'if os.getenv("IGOR_FOO") == "true":\n'
                "    raise NotImplementedError\n"
            )
            f.flush()
            issues = _check_stubs_near_gate("IGOR_FOO", f.name)
        assert len(issues) >= 1

    def test_clean_code_no_issues(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                'if os.getenv("IGOR_FOO") == "true":\n'
                "    result = do_real_work()\n"
                "    return result\n"
            )
            f.flush()
            issues = _check_stubs_near_gate("IGOR_FOO", f.name)
        assert issues == []

    def test_except_pass_not_flagged(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                'if os.getenv("IGOR_FOO") == "true":\n'
                "    try:\n"
                "        risky()\n"
                "    except Exception:\n"
                "        pass\n"
            )
            f.flush()
            issues = _check_stubs_near_gate("IGOR_FOO", f.name)
        assert issues == []


class TestRunWiringCheck:
    def test_all_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "switches.cfg"
            cfg.write_text("IGOR_TEST_FEATURE=true\n")
            src = Path(tmpdir) / "src"
            src.mkdir()
            (src / "main.py").write_text(
                'if os.getenv("IGOR_TEST_FEATURE") == "true":\n' "    run_feature()\n"
            )
            ok, issues = run_wiring_check(cfg, src)
        assert len(ok) == 1
        assert len(issues) == 0

    def test_unreferenced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "switches.cfg"
            cfg.write_text("IGOR_GHOST=true\n")
            src = Path(tmpdir) / "src"
            src.mkdir()
            (src / "main.py").write_text("x = 1\n")
            ok, issues = run_wiring_check(cfg, src)
        assert len(issues) >= 1
        assert any("UNREFERENCED" in i for i in issues)

    def test_stub_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "switches.cfg"
            cfg.write_text("IGOR_STUB=true\n")
            src = Path(tmpdir) / "src"
            src.mkdir()
            (src / "main.py").write_text(
                'if os.getenv("IGOR_STUB") == "true":\n' "    # TODO: wire this up\n"
            )
            ok, issues = run_wiring_check(cfg, src)
        assert any("STUB_NEAR_GATE" in i for i in issues)

    def test_disabled_switch_not_checked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "switches.cfg"
            cfg.write_text("IGOR_OFF=false\n")
            src = Path(tmpdir) / "src"
            src.mkdir()
            (src / "main.py").write_text("x = 1\n")
            ok, issues = run_wiring_check(cfg, src)
        assert len(issues) == 0

    def test_live_config(self):
        """Run against the actual switches config — should pass."""
        ok, issues = run_wiring_check()
        assert len(issues) == 0, f"Live config has wiring issues: {issues}"
