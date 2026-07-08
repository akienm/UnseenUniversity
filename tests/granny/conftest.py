"""Shared hermetic setup for the granny test suite.

Granny's stall/dispatch state lives in flat files under ``~/.granny/`` via the
module-level ``stall_state._STATE`` / ``_MRU`` / ``_LAST_DISPATCH`` paths. Any
test that drives ``run_once`` hits the very first guard in the daemon —
``if is_stalled(): return`` — which reads ``_STATE`` off the real box. If the
operator's granny happens to be parked (stalled=True), ``run_once`` returns
before dispatching and every "``_dispatch_bus``/``_setstatus_direct`` called
once" assertion fails — an env-dependent red that passes only on a box whose
real stall file is clean (T-granny-dispatch-tests-preexisting-failures).

This autouse fixture redirects all three stall-state paths to a per-test tmp
dir, so ``is_stalled()`` reads a fresh (nonexistent → default False) file and
the suite is isolated from the operator's real granny state. A test that needs
the stalled path sets it explicitly on its own tmp file (as
``test_granny_stall.py`` does), which simply overrides these bindings inside
the test body.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.granny import stall_state


@pytest.fixture(autouse=True)
def _hermetic_stall_state(tmp_path, monkeypatch):
    """Redirect granny's stall-state flat files to a per-test tmp dir."""
    monkeypatch.setattr(stall_state, "_STATE", tmp_path / "stall_state.json")
    monkeypatch.setattr(stall_state, "_MRU", tmp_path / "mru.json")
    monkeypatch.setattr(stall_state, "_LAST_DISPATCH", tmp_path / "last_dispatch.json")
    yield
