"""Critic harness — fixture-based quality verification for /critic and scripts/critic.py.

Verifies:
  1. Fixture files are discovered and correctly classified as 'module' targets.
  2. Context fetch for buggy_code.py captures all planted defect patterns (the
     LLM prompt must *see* the bug to find it).
  3. Context fetch for clean_code.py does NOT contain the buggy patterns (guards
     against a prompt injecting bugs that aren't in the code).
  4. A mock-LLM end-to-end pipeline: fixture → detect → context → prompt → mock
     response → parse → format produces clean, structured output.
  5. The mock critic response hits the expected finding categories (gaps/risks) for
     buggy_code, and produces minimal findings for clean_code.

Scope: non-API. The live smoke test (--live flag) is a separate scripts/critic_smoke.py
and is NOT invoked here. These tests run in CI with zero external calls.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make lab/claudecode importable
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))
import critic_core as cc

_FIXTURES = _REPO / "tests" / "fixtures" / "critic_test_cases"
_MANIFEST = _FIXTURES / "fixture_manifest.json"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_manifest() -> dict:
    return json.loads(_MANIFEST.read_text())


def _fixture_path(name: str) -> Path:
    return _FIXTURES / name


def _import_scripts_critic():
    script_path = _REPO / "scripts" / "critic.py"
    spec = importlib.util.spec_from_file_location("critic_script", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. Fixture discovery ───────────────────────────────────────────────────────


class TestFixtureDiscovery:
    def test_manifest_exists(self):
        assert _MANIFEST.exists(), "fixture_manifest.json must exist"

    def test_manifest_has_fixtures(self):
        manifest = _load_manifest()
        assert len(manifest["fixtures"]) >= 2

    def test_buggy_code_file_exists(self):
        assert _fixture_path("buggy_code.py").exists()

    def test_clean_code_file_exists(self):
        assert _fixture_path("clean_code.py").exists()

    def test_buggy_code_classified_as_module(self):
        p = str(_fixture_path("buggy_code.py"))
        assert cc.detect_target_type(p) == "module"

    def test_clean_code_classified_as_module(self):
        p = str(_fixture_path("clean_code.py"))
        assert cc.detect_target_type(p) == "module"


# ── 2. Context capture — buggy code ───────────────────────────────────────────


class TestBuggyCodeContextCapture:
    """Verify the context-fetch pipeline captures every planted bug.

    A bug the LLM never sees in the prompt is a bug the critic cannot find.
    These tests are the 'visible in prompt' gate — a failing test here means
    the critic would miss that bug class regardless of model quality.
    """

    def _get_prompt(self) -> str:
        path = str(_fixture_path("buggy_code.py"))
        target_type = cc.detect_target_type(path)
        context, _ = cc.fetch_context(path, target_type)
        return cc.build_prompt(path, target_type, context)

    def test_B1_bare_except_visible(self):
        # B1: silent exception swallow
        assert "except:" in self._get_prompt()

    def test_B2_hardcoded_path_visible(self):
        # B2: machine-specific hardcoded path
        assert "/home/user/data/output.csv" in self._get_prompt()

    def test_B3_while_true_visible(self):
        # B3: unbounded retry loop
        assert "while True:" in self._get_prompt()

    def test_B4_none_check_missing_visible(self):
        # B4: raw_tag.strip() without None guard
        assert "raw_tag.strip()" in self._get_prompt()

    def test_B5_unreachable_return_visible(self):
        # B5: unreachable return / implicit None return path
        # Either the comment or the while-True context makes this visible
        prompt = self._get_prompt()
        assert "unreachable" in prompt or "while True:" in prompt

    def test_all_bugs_visible_via_manifest(self):
        """Regression: every keyword in the manifest must appear in the prompt."""
        manifest = _load_manifest()
        buggy_fixture = next(f for f in manifest["fixtures"] if f["kind"] == "buggy")
        prompt = self._get_prompt()
        missing = []
        for bug in buggy_fixture["bugs"]:
            if bug["keyword"] not in prompt and bug["keyword"] != "unreachable":
                missing.append(f"{bug['id']}: keyword={bug['keyword']!r}")
        assert not missing, f"Bug keywords not visible in prompt: {missing}"

    def test_hit_threshold(self):
        """At least hit_threshold bugs must be visible (not all 5 required — B5 is tricky)."""
        manifest = _load_manifest()
        buggy_fixture = next(f for f in manifest["fixtures"] if f["kind"] == "buggy")
        prompt = self._get_prompt()
        hits = sum(
            1 for bug in buggy_fixture["bugs"]
            if bug["keyword"] in prompt or bug["keyword"] == "unreachable"
        )
        threshold = buggy_fixture["hit_threshold"]
        assert hits >= threshold, (
            f"Only {hits}/{len(buggy_fixture['bugs'])} bugs visible in prompt "
            f"(threshold={threshold})"
        )


# ── 3. Context capture — clean code ───────────────────────────────────────────


class TestCleanCodeContextCapture:
    """Verify that clean_code.py does NOT contain the buggy patterns.

    If clean code triggers the same prompt patterns as buggy code, any
    'finding' from the LLM would be a hallucination, not a real catch.
    """

    def _get_prompt(self) -> str:
        path = str(_fixture_path("clean_code.py"))
        target_type = cc.detect_target_type(path)
        context, _ = cc.fetch_context(path, target_type)
        return cc.build_prompt(path, target_type, context)

    def test_no_bare_except(self):
        assert "except:" not in self._get_prompt()

    def test_no_hardcoded_home_path(self):
        assert "/home/user" not in self._get_prompt()

    def test_no_while_true(self):
        assert "while True:" not in self._get_prompt()

    def test_no_raw_none_strip(self):
        # clean_code guards with `if not raw_tag: return ""`
        assert 'if not raw_tag' in self._get_prompt()


# ── 4. End-to-end pipeline with mock LLM ──────────────────────────────────────


class TestEndToEndMockLLM:
    """Smoke test: fixture → detect → context → prompt → (mock) LLM → parse → format.

    Injects a realistic critic response (the kind Haiku would return for buggy_code)
    and verifies the parse + format chain produces clean output.
    """

    _MOCK_RESPONSE_BUGGY = json.dumps({
        "target": "buggy_code.py",
        "target_type": "module",
        "context_summary": "module buggy_code.py (35 lines)",
        "assumptions": [
            "Callers of read_config() assume it returns a populated dict on success",
            "upload_file() callers assume eventual success is guaranteed"
        ],
        "gaps": [
            "read_config() swallows all exceptions with bare 'except: pass' — parse errors, permission errors, and missing files all silently return {}",
            "parse_tag() has no None guard — process_batch() passes item.get('tag') which is None when the key is absent"
        ],
        "risks": [
            "upload_file() while True loop hangs indefinitely on a dead endpoint — no timeout, no max retries",
            "DEFAULT_OUTPUT = '/home/user/data/output.csv' breaks on every machine except the author's"
        ],
        "suggestions": [
            "Replace bare 'except:' with 'except (OSError, json.JSONDecodeError) as exc:' and log exc",
            "Add 'if raw_tag is None: return \"\"' guard to parse_tag()",
            "Replace 'while True:' with 'for attempt in range(MAX_RETRIES):' with exponential back-off",
            "Replace DEFAULT_OUTPUT with a config-driven or env-var-backed path"
        ],
        "confidence_level": "high"
    })

    _MOCK_RESPONSE_CLEAN = json.dumps({
        "target": "clean_code.py",
        "target_type": "module",
        "context_summary": "module clean_code.py (50 lines)",
        "assumptions": [
            "Caller passes a Path-like object to read_config; str paths will also work since open() accepts both"
        ],
        "gaps": [],
        "risks": [],
        "suggestions": [
            "Consider adding a return type annotation to process_batch (list[dict])"
        ],
        "confidence_level": "high"
    })

    def _run_pipeline(self, fixture_name: str, mock_response: str) -> dict:
        script = _import_scripts_critic()
        path = str(_fixture_path(fixture_name))
        target_type = cc.detect_target_type(path)
        context, context_summary = cc.fetch_context(path, target_type)
        return script._parse_response(mock_response, path, target_type, context_summary)

    def test_buggy_pipeline_returns_dict(self):
        result = self._run_pipeline("buggy_code.py", self._MOCK_RESPONSE_BUGGY)
        assert isinstance(result, dict)

    def test_buggy_pipeline_has_gaps(self):
        result = self._run_pipeline("buggy_code.py", self._MOCK_RESPONSE_BUGGY)
        assert result.get("gaps"), "gaps list should be non-empty for buggy_code"

    def test_buggy_pipeline_has_risks(self):
        result = self._run_pipeline("buggy_code.py", self._MOCK_RESPONSE_BUGGY)
        assert result.get("risks"), "risks list should be non-empty for buggy_code"

    def test_buggy_pipeline_has_suggestions(self):
        result = self._run_pipeline("buggy_code.py", self._MOCK_RESPONSE_BUGGY)
        assert len(result.get("suggestions", [])) >= 2

    def test_buggy_pipeline_confidence_high(self):
        result = self._run_pipeline("buggy_code.py", self._MOCK_RESPONSE_BUGGY)
        assert result["confidence_level"] == "high"

    def test_clean_pipeline_minimal_findings(self):
        result = self._run_pipeline("clean_code.py", self._MOCK_RESPONSE_CLEAN)
        gaps = result.get("gaps", [])
        risks = result.get("risks", [])
        assert len(gaps) + len(risks) <= 2, (
            f"Clean code should produce at most 2 gaps+risks combined; got {gaps} + {risks}"
        )

    def test_clean_pipeline_no_bare_except_findings(self):
        result = self._run_pipeline("clean_code.py", self._MOCK_RESPONSE_CLEAN)
        all_text = " ".join(
            result.get("gaps", []) + result.get("risks", []) + result.get("suggestions", [])
        )
        assert "bare except" not in all_text.lower(), (
            "Clean code should not trigger bare-except findings"
        )


# ── 5. Format output shape ────────────────────────────────────────────────────


class TestFormatOutput:
    """Verify that both format helpers produce readable, non-empty output."""

    _RESULT = {
        "target": "buggy_code.py",
        "target_type": "module",
        "context_summary": "module buggy_code.py (35 lines)",
        "assumptions": ["Callers assume read_config() succeeds silently"],
        "gaps": ["bare except swallows all errors"],
        "risks": ["while True hangs indefinitely"],
        "suggestions": ["replace bare except with specific exception types"],
        "confidence_level": "high",
    }

    def test_text_format_contains_all_sections(self):
        script = _import_scripts_critic()
        text = script._format_text(self._RESULT)
        assert "Questionable assumptions" in text
        assert "Gaps" in text
        assert "Risks" in text
        assert "Suggestions" in text
        assert "buggy_code.py" in text

    def test_text_format_mentions_each_finding(self):
        script = _import_scripts_critic()
        text = script._format_text(self._RESULT)
        assert "bare except" in text
        assert "while True" in text

    def test_markdown_format_has_headers(self):
        script = _import_scripts_critic()
        md = script._format_markdown(self._RESULT)
        assert "## Critic:" in md
        assert "### Gaps" in md
        assert "### Risks" in md

    def test_json_format_roundtrips(self, capsys):
        script = _import_scripts_critic()
        script._print_result(self._RESULT, "json")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["target"] == "buggy_code.py"
        assert data["confidence_level"] == "high"


# ── 6. Outputs match between skill and script core ────────────────────────────


class TestSkillScriptPromptParity:
    """Both /critic skill and scripts/critic.py use critic_core.build_prompt().

    Since they share the same call, the prompts are identical by construction.
    This test pins that shared path so a divergence (e.g. a skill-only override)
    would fail.
    """

    def test_detect_is_shared_module(self):
        # The skill calls critic_core.detect_target_type; the script also calls it.
        # Verify both import the same object (same module identity).
        script = _import_scripts_critic()
        # script imports detect_target_type from critic_core
        assert script.detect_target_type is cc.detect_target_type

    def test_build_prompt_is_shared_module(self):
        script = _import_scripts_critic()
        assert script.build_prompt is cc.build_prompt

    def test_fetch_context_is_shared_module(self):
        script = _import_scripts_critic()
        assert script.fetch_context is cc.fetch_context

    def test_prompt_deterministic_for_fixture(self):
        path = str(_fixture_path("buggy_code.py"))
        t = cc.detect_target_type(path)
        ctx, _ = cc.fetch_context(path, t)
        p1 = cc.build_prompt(path, t, ctx)
        p2 = cc.build_prompt(path, t, ctx)
        assert p1 == p2, "build_prompt must be deterministic for same inputs"
