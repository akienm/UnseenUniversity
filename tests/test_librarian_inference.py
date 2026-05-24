"""Tests for Librarian inference routing — T-librarian-inference-routing."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from unseen_university.devices.librarian.inference import InferenceRouter, ModelSelection


@pytest.fixture()
def config_file(tmp_path):
    cfg = tmp_path / "model_config.yaml"
    cfg.write_text(textwrap.dedent("""\
        tiers:
          0:
            name: routine
            models:
              - name: qwen2.5:8b
                backend: ollama
          1:
            name: heavy
            models:
              - name: qwen2.5:32b
                backend: ollama
          2:
            name: cloud
            models:
              - name: claude-haiku-4-5-20251001
                backend: anthropic
              - name: claude-sonnet-4-6
                backend: anthropic

        task_type_tiers:
          chat: 0
          reply: 0
          summarize: 1
          research: 1
          reason: 2
          plan: 2
        """))
    return cfg


class TestTierSelection:
    def test_chat_selects_tier_0(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("chat")
        assert sel.tier == 0
        assert sel.model == "qwen2.5:8b"
        assert sel.backend == "ollama"

    def test_summarize_selects_tier_1(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("summarize")
        assert sel.tier == 1
        assert sel.model == "qwen2.5:32b"

    def test_reason_selects_tier_2(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("reason")
        assert sel.tier == 2
        assert sel.backend == "anthropic"

    def test_unknown_task_defaults_to_tier_0(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("totally_unknown_task")
        assert sel.tier == 0

    def test_case_insensitive(self, config_file):
        router = InferenceRouter(config_path=config_file)
        assert router.select("CHAT").tier == 0
        assert router.select("Summarize").tier == 1

    def test_returns_model_selection_dataclass(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("chat")
        assert isinstance(sel, ModelSelection)
        assert sel.task_type == "chat"

    def test_tier_name_populated(self, config_file):
        router = InferenceRouter(config_path=config_file)
        assert router.select("chat").tier_name == "routine"
        assert router.select("summarize").tier_name == "heavy"
        assert router.select("reason").tier_name == "cloud"


class TestExplicitTierSelect:
    def test_select_tier_bypasses_task_type(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select_tier(2)
        assert sel.tier == 2
        assert sel.backend == "anthropic"

    def test_select_tier_unknown_falls_back_to_0(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select_tier(99)
        assert sel.tier == 0

    def test_tier_for_returns_number(self, config_file):
        router = InferenceRouter(config_path=config_file)
        assert router.tier_for("summarize") == 1
        assert router.tier_for("chat") == 0
        assert router.tier_for("unknown") == 0


class TestConfigHandling:
    def test_missing_config_falls_back_gracefully(self, tmp_path):
        router = InferenceRouter(config_path=tmp_path / "nonexistent.yaml")
        sel = router.select("chat")
        # Falls back to hardcoded default in empty-config path
        assert isinstance(sel, ModelSelection)
        assert sel.model == "qwen2.5:8b"

    def test_reload_rereads_config(self, config_file):
        router = InferenceRouter(config_path=config_file)
        _ = router.select("chat")  # loads config
        # Update the YAML
        import yaml

        data = yaml.safe_load(config_file.read_text())
        data["task_type_tiers"]["chat"] = 1
        config_file.write_text(yaml.dump(data))
        router.reload()
        assert router.select("chat").tier == 1

    def test_uses_default_config_path_when_none(self):
        router = InferenceRouter()
        # The real config file exists in the package — just verify no crash
        sel = router.select("chat")
        assert isinstance(sel, ModelSelection)

    def test_no_yaml_package_falls_back(self, tmp_path, monkeypatch):
        import sys

        # Patch yaml to be unavailable
        real_yaml = sys.modules.get("yaml")
        monkeypatch.setitem(sys.modules, "yaml", None)
        router = InferenceRouter(config_path=tmp_path / "cfg.yaml")
        sel = router.select("chat")
        assert isinstance(sel, ModelSelection)
        if real_yaml:
            monkeypatch.setitem(sys.modules, "yaml", real_yaml)


class TestEscalationPath:
    def test_cloud_tier_has_anthropic_backend(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("plan")
        assert sel.backend == "anthropic"

    def test_cloud_tier_model_name(self, config_file):
        router = InferenceRouter(config_path=config_file)
        sel = router.select("reason")
        assert "claude" in sel.model.lower()
