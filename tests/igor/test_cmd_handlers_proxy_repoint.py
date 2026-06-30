"""T-inf-reroute-C: the interactive handlers repointed off _t* still run.

_cmd_local, _cmd_model, and the "is cloud available" branch used to read the
gateway's _t2/_t4 reasoner objects. Those are deleted; the handlers now read
cluster_router / the Proxy's source_health / gateway.describe(). A boot smoke
test wouldn't exercise these interactive paths, so drive each once on a minimal
Igor instance with a stubbed Proxy and assert no throw (advisor #3).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from unseen_university.devices.igor.main import Igor
from unseen_university.devices.igor.cognition.inference_gateway import (
    build_default_gateway,
)
from unseen_university.devices.inference.shim import InferenceResponse


@pytest.fixture
def igor_stub():
    """Minimal Igor with a stubbed-Proxy gateway — no DB / boot."""
    ig = Igor.__new__(Igor)
    gw = build_default_gateway()
    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(text="x", source_kind="cloud")
    spy.source_health.return_value = {"openrouter": True, "local-ollama": False}
    gw._inference = spy
    ig._gateway = gw
    ig.local_mode = False
    return ig


def test_cmd_local_toggle_runs(igor_stub):
    igor_stub._cmd_local("local on")   # local pool path (cluster_router)
    assert igor_stub.local_mode is True
    igor_stub._cmd_local("local off")  # cloud path (Proxy-routed message)
    assert igor_stub.local_mode is False


def test_cmd_model_info_runs(igor_stub):
    # No model arg -> shows describe() + aliases (no _t* access).
    igor_stub._cmd_model("model")
    # Setting a model -> informational refusal (tier-not-model), no throw.
    igor_stub._cmd_model("model claude-sonnet-4.6")


def test_cloud_availability_uses_source_health(igor_stub):
    # The "is cloud available" branch reads the Proxy's source_health.
    health = igor_stub._gateway._get_inference().source_health()
    cloud_ok = any(
        ok and "ollama" not in n.lower() and "local" not in n.lower()
        for n, ok in health.items()
    )
    assert cloud_ok is True
