"""Tests for T-inference-models-versioned — versioned history in ModelsRegistry."""

from __future__ import annotations

from devices.inference.models_registry import ModelSpec, ModelsRegistry, _SEED


def _spec(model_id: str = "test/model", **kwargs) -> ModelSpec:
    base = dict(
        model_id=model_id,
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.1,
        output_cost_per_1m=0.4,
        context_window=128_000,
        created_at="2026-06-01T00:00:00Z",
    )
    base.update(kwargs)
    return ModelSpec(**base)


class TestVersionHistory:
    def test_empty_history_before_any_update(self):
        reg = ModelsRegistry([_spec()])
        assert reg.list_model_history("test/model") == []

    def test_update_model_archives_old_entry(self):
        old = _spec(notes="old notes", created_at="2026-06-01T00:00:00Z")
        reg = ModelsRegistry([old])
        new = _spec(notes="new notes", created_at="2026-06-15T00:00:00Z")
        reg.update_model("test/model", new)

        history = reg.list_model_history("test/model")
        assert len(history) == 1
        assert history[0]["notes"] == "old notes"
        assert history[0]["created_at"] == "2026-06-01T00:00:00Z"
        assert "retired_at" in history[0]

    def test_facia_row_reflects_new_spec_after_update(self):
        reg = ModelsRegistry([_spec(notes="v1")])
        reg.update_model("test/model", _spec(notes="v2"))
        assert reg.get("test/model").notes == "v2"

    def test_facia_key_stable_across_updates(self):
        reg = ModelsRegistry([_spec()])
        reg.update_model("test/model", _spec(notes="v2"))
        reg.update_model("test/model", _spec(notes="v3"))
        assert reg.get("test/model") is not None
        assert reg.get("test/model").notes == "v3"

    def test_history_ordered_oldest_first(self):
        reg = ModelsRegistry([_spec(notes="v1")])
        reg.update_model("test/model", _spec(notes="v2"))
        reg.update_model("test/model", _spec(notes="v3"))
        history = reg.list_model_history("test/model")
        assert len(history) == 2
        assert history[0]["notes"] == "v1"
        assert history[1]["notes"] == "v2"

    def test_unknown_model_returns_empty_history(self):
        reg = ModelsRegistry([_spec()])
        assert reg.list_model_history("no/such/model") == []

    def test_seed_data_has_created_at_on_all_entries(self):
        for spec in _SEED:
            assert spec.created_at, f"{spec.model_id} missing created_at"

    def test_update_unknown_model_registers_without_archiving(self):
        reg = ModelsRegistry([])
        new = _spec("brand/new")
        reg.update_model("brand/new", new)
        assert reg.get("brand/new") is new
        assert reg.list_model_history("brand/new") == []
