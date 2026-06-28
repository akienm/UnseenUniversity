"""Tests for T-inference-models-autoversion — ModelVersionChecker."""

from __future__ import annotations

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.version_checker import ModelVersionChecker


def _make_spec(model_id: str, created_at: str = "2026-06-01T00:00:00Z") -> ModelSpec:
    return ModelSpec(
        model_id=model_id,
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.07,
        output_cost_per_1m=0.28,
        context_window=128_000,
        notes="original",
        created_at=created_at,
    )


def _reg(*specs: ModelSpec) -> ModelsRegistry:
    return ModelsRegistry(list(specs))


class TestModelVersionChecker:
    def test_newer_or_date_triggers_update(self):
        reg = _reg(_make_spec("qwen/qwen3-coder", "2026-06-01T00:00:00Z"))
        # 2026-06-15 epoch > 2026-06-01 epoch
        or_listing = [{"id": "qwen/qwen3-coder", "created": 1781568000}]  # 2026-06-16
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)

        updated = checker.check()

        assert "qwen/qwen3-coder" in updated
        assert reg.get("qwen/qwen3-coder").created_at != "2026-06-01T00:00:00Z"

    def test_same_date_is_noop(self):
        # epoch 1780272000 = 2026-06-01T00:00:00Z
        reg = _reg(_make_spec("qwen/qwen3-coder", "2026-06-01T00:00:00Z"))
        or_listing = [{"id": "qwen/qwen3-coder", "created": 1780272000}]
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)

        updated = checker.check()

        assert updated == []
        assert reg.list_model_history("qwen/qwen3-coder") == []

    def test_older_or_date_is_noop(self):
        reg = _reg(_make_spec("qwen/qwen3-coder", "2026-06-15T00:00:00Z"))
        or_listing = [{"id": "qwen/qwen3-coder", "created": 1780272000}]  # 2026-06-01
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)

        updated = checker.check()

        assert updated == []

    def test_model_not_in_or_listing_is_skipped(self):
        reg = _reg(_make_spec("local/my-model", "2026-06-01T00:00:00Z"))
        or_listing = []  # nothing in OR
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)

        updated = checker.check()

        assert updated == []

    def test_update_archives_old_entry(self):
        reg = _reg(_make_spec("deepseek/deepseek-v4-flash", "2026-06-01T00:00:00Z"))
        or_listing = [{"id": "deepseek/deepseek-v4-flash", "created": 1781568000}]
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)
        checker.check()

        history = reg.list_model_history("deepseek/deepseek-v4-flash")
        assert len(history) == 1
        assert history[0]["created_at"] == "2026-06-01T00:00:00Z"
        assert "retired_at" in history[0]

    def test_fetch_failure_returns_empty_and_does_not_raise(self):
        reg = _reg(_make_spec("qwen/qwen3-coder", "2026-06-01T00:00:00Z"))

        def bad_fetch():
            raise ConnectionError("network down")

        checker = ModelVersionChecker(reg, _fetch_fn=bad_fetch)
        updated = checker.check()  # must not raise

        assert updated == []
        assert reg.list_model_history("qwen/qwen3-coder") == []

    def test_multiple_models_only_updates_stale_ones(self):
        reg = _reg(
            _make_spec("qwen/qwen3-coder", "2026-06-01T00:00:00Z"),
            _make_spec("deepseek/deepseek-v4-flash", "2026-07-01T00:00:00Z"),
        )
        # qwen has newer OR date (2026-06-16); deepseek has older OR date (2026-06-01)
        or_listing = [
            {"id": "qwen/qwen3-coder", "created": 1781568000},       # 2026-06-16
            {"id": "deepseek/deepseek-v4-flash", "created": 1780272000},  # 2026-06-01
        ]
        checker = ModelVersionChecker(reg, _fetch_fn=lambda: or_listing)
        updated = checker.check()

        assert updated == ["qwen/qwen3-coder"]
        assert reg.list_model_history("deepseek/deepseek-v4-flash") == []
