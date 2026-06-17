"""Tests for critic_core.py and scripts/critic.py (T-critic-skill-implementation, T-critic-script-implementation)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# Make lab/claudecode importable
sys.path.insert(0, str(Path(__file__).parent.parent / "devlab" / "claudecode"))
import critic_core as cc


class TestDetectTargetType:
    def test_ticket_id(self):
        assert cc.detect_target_type("T-my-ticket") == "ticket"
        assert cc.detect_target_type("T-foo-bar-baz") == "ticket"

    def test_module_path_suffix(self):
        assert cc.detect_target_type("lab/claudecode/critic_core.py") == "module"
        assert cc.detect_target_type("devices/granny/daemon.py") == "module"

    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert cc.detect_target_type(str(f)) == "module"

    def test_symbol_single_word(self):
        assert cc.detect_target_type("detect_target_type") == "symbol"
        assert cc.detect_target_type("PluginDaemon") == "symbol"

    def test_free_text(self):
        assert cc.detect_target_type("does this design handle disconnect?") == "free"
        assert cc.detect_target_type("what about error handling") == "free"


class TestFetchContext:
    def test_free_type_returns_target(self):
        ctx, summary = cc.fetch_context("some free text", "free")
        assert "free text" in ctx or "some free text" in ctx
        assert summary

    def test_module_reads_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    return 42\n")
        ctx, summary = cc.fetch_context(str(f), "module")
        assert "def foo" in ctx
        assert "test.py" in summary

    def test_module_missing_file_returns_gracefully(self):
        ctx, summary = cc.fetch_context("/nonexistent/path/to/file.py", "module")
        assert "module" in summary  # graceful: summary mentions module

    def test_ticket_fetch_graceful_on_missing(self):
        # T-nonexistent should not raise
        ctx, summary = cc.fetch_context("T-nonexistent-xyz-9999", "ticket")
        assert summary  # returns something


class TestBuildPrompt:
    def test_prompt_contains_target(self):
        prompt = cc.build_prompt("T-test", "ticket", "some context")
        assert "T-test" in prompt
        assert "ticket" in prompt
        assert "some context" in prompt

    def test_prompt_contains_schema(self):
        prompt = cc.build_prompt("foo", "symbol", "context")
        assert "assumptions" in prompt
        assert "risks" in prompt
        assert "suggestions" in prompt

    def test_system_prompt_exists(self):
        assert len(cc.CRITIC_SYSTEM) > 50
        assert "critic" in cc.CRITIC_SYSTEM.lower()


class TestCache:
    def test_cache_put_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "_CACHE_DIR", tmp_path / "cache")
        result = {"target": "foo", "assumptions": ["a"], "confidence_level": "high"}
        cc.cache_put("foo", result)
        cached = cc.cache_get("foo")
        assert cached == result

    def test_cache_miss_on_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "_CACHE_DIR", tmp_path / "cache")
        assert cc.cache_get("definitely-not-cached-zzzz") is None

    def test_cache_expires(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "_CACHE_DIR", tmp_path / "cache")
        result = {"target": "bar", "assumptions": [], "confidence_level": "low"}
        cc.cache_put("bar", result)
        # Back-date the cache file
        key = cc._cache_key("bar")
        import os
        old = time.time() - cc._CACHE_TTL - 10
        os.utime(key, (old, old))
        assert cc.cache_get("bar") is None


class TestCriticCoreCLI:
    def test_detect_command(self, capsys):
        from critic_core import _cli
        rc = _cli(["detect", "T-my-ticket"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "ticket"

    def test_detect_symbol(self, capsys):
        from critic_core import _cli
        rc = _cli(["detect", "run_forever"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "symbol"


class TestScriptsCritic:
    """Integration tests for scripts/critic.py (without calling the real API)."""

    def _import_script(self):
        import importlib.util, sys
        script_path = Path(__file__).parent.parent / "scripts" / "critic.py"
        spec = importlib.util.spec_from_file_location("critic_script", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_format_text(self):
        script = self._import_script()
        result = {
            "target": "foo",
            "target_type": "symbol",
            "context_summary": "symbol foo in 1 file",
            "assumptions": ["assumes foo is always called with valid input"],
            "gaps": [],
            "risks": ["could raise KeyError on missing key"],
            "suggestions": ["add input validation"],
            "confidence_level": "medium",
        }
        text = script._format_text(result)
        assert "foo" in text
        assert "assumes foo is always called" in text
        assert "KeyError" in text

    def test_format_markdown(self):
        script = self._import_script()
        result = {
            "target": "T-test",
            "target_type": "ticket",
            "context_summary": "ticket T-test",
            "assumptions": ["a1"],
            "gaps": ["g1"],
            "risks": [],
            "suggestions": [],
            "confidence_level": "low",
        }
        md = script._format_markdown(result)
        assert "## Critic:" in md
        assert "a1" in md
        assert "g1" in md

    def test_format_json(self, capsys):
        script = self._import_script()
        result = {"target": "bar", "confidence_level": "high", "assumptions": []}
        script._print_result(result, "json")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["target"] == "bar"

    def test_parse_response_handles_fences(self):
        script = self._import_script()
        raw = '```json\n{"assumptions": [], "risks": ["r1"], "confidence_level": "low"}\n```'
        result = script._parse_response(raw, "t", "free", "summary")
        assert result["risks"] == ["r1"]
        assert result["target"] == "t"

    def test_parse_response_handles_bad_json(self):
        script = self._import_script()
        result = script._parse_response("not json at all", "t", "free", "summary")
        assert result["confidence_level"] == "low"
        assert "not json at all" in result["risks"][0]
