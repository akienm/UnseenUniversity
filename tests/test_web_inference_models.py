"""Tests for /api/inference/models/{id}/history and /inference/models routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry


def _make_app():
    import unseen_university.devices.web_server.server as _srv
    with patch("unseen_university.devices.web_server.server._init_comms"):
        return _srv._make_app()


def _reg_with_history() -> ModelsRegistry:
    spec = ModelSpec(
        model_id="test/model-v1",
        tier="worker",
        input_cost_per_1m=0.07,
        output_cost_per_1m=0.28,
        context_window=128_000,
        notes="original",
        created_at="2026-06-01T00:00:00Z",
    )
    reg = ModelsRegistry([spec])
    reg.update_model(
        "test/model-v1",
        ModelSpec(
            model_id="test/model-v1",
            tier="worker",
            input_cost_per_1m=0.09,
            output_cost_per_1m=0.36,
            context_window=128_000,
            notes="updated",
            created_at="2026-06-15T00:00:00Z",
        ),
    )
    return reg


class TestApiInferenceModelHistory:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=_reg_with_history()):
            with TestClient(app) as client:
                resp = client.get("/api/inference/models/test%2Fmodel-v1/history")
        assert resp.status_code == 200

    def test_returns_history_list(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=_reg_with_history()):
            with TestClient(app) as client:
                data = client.get("/api/inference/models/test%2Fmodel-v1/history").json()
        assert "history" in data
        assert data["count"] == 1
        assert data["history"][0]["notes"] == "original"
        assert "retired_at" in data["history"][0]

    def test_empty_history_returns_empty_array(self):
        from starlette.testclient import TestClient
        reg = ModelsRegistry([ModelSpec(
            model_id="fresh/model", tier="worker",
            input_cost_per_1m=0.1, output_cost_per_1m=0.4, context_window=128_000,
            created_at="2026-06-01T00:00:00Z",
        )])
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=reg):
            with TestClient(app) as client:
                data = client.get("/api/inference/models/fresh%2Fmodel/history").json()
        assert data["history"] == []
        assert data["count"] == 0

    def test_registry_unavailable_returns_empty_gracefully(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=None):
            with TestClient(app) as client:
                data = client.get("/api/inference/models/any%2Fmodel/history").json()
        assert data["history"] == []
        assert "error" in data


class TestPageInferenceModels:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=_reg_with_history()):
            with TestClient(app) as client:
                resp = client.get("/inference/models")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_renders_model_id(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=_reg_with_history()):
            with TestClient(app) as client:
                html = client.get("/inference/models").text
        assert "test/model-v1" in html

    def test_renders_history_section(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=_reg_with_history()):
            with TestClient(app) as client:
                html = client.get("/inference/models").text
        assert "prior version" in html

    def test_registry_unavailable_shows_message(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._inference_registry", return_value=None):
            with TestClient(app) as client:
                html = client.get("/inference/models").text
        assert "unavailable" in html.lower()
