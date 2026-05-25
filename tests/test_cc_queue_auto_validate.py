"""Tests for cc_queue auto-validate logic (T-auto-validate)."""

from unittest.mock import patch, call
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lab.claudecode.cc_queue as q


def _ticket(**overrides):
    base = {
        "id": "T-test",
        "title": "test ticket",
        "status": "awaiting_validation",
        "size": "S",
        "worker": "igor",
        "result": "pe_chain autonomous: pass",
        "description": "Add a helper function.\n\n**Affected files:** devices/igor/tools/helper.py",
        "decision_id": None,
        "gate": None,
        "completed_at": "2026-05-18T00:00:00Z",
        "auto_validated": False,
    }
    base.update(overrides)
    return base


def _mock_side_effects():
    """Return a context manager patching all I/O in _try_auto_validate."""
    return [
        patch.object(q, "_log"),
        patch.object(q, "_decision_rollup"),
        patch.object(q, "_ungate_dependents"),
        patch.object(q, "_save"),
        patch.object(q, "_prepend_closed_ticket"),
        patch.object(q, "_append_to_todays_slate"),
    ]


class TestTryAutoValidate:
    def _run(self, ticket, tasks=None):
        if tasks is None:
            tasks = [ticket]
        patches = _mock_side_effects()
        mocks = [p.start() for p in patches]
        try:
            result = q._try_auto_validate(tasks, ticket)
        finally:
            for p in patches:
                p.stop()
        return result, mocks

    def test_passes_all_criteria(self):
        t = _ticket()
        result, mocks = self._run(t)
        assert result is True
        assert t["status"] == "closed"
        assert t["auto_validated"] is True

    def test_skip_large_size(self):
        t = _ticket(size="L")
        result, _ = self._run(t)
        assert result is False
        assert t["status"] == "awaiting_validation"

    def test_skip_xl_size(self):
        t = _ticket(size="XL")
        result, _ = self._run(t)
        assert result is False

    def test_skip_high_inertia_brainstem(self):
        t = _ticket(description="Affected files: devices/igor/brainstem/core.py")
        result, _ = self._run(t)
        assert result is False

    def test_skip_high_inertia_memory_models(self):
        t = _ticket(description="Affected files: devices/igor/memory/models.py")
        result, _ = self._run(t)
        assert result is False

    def test_skip_high_inertia_reasoners_base(self):
        t = _ticket(description="Affected files: cognition/reasoners/base.py")
        result, _ = self._run(t)
        assert result is False

    def test_skip_result_contains_fail(self):
        t = _ticket(
            result="pe_chain autonomous: fail — test_helper.py::test_add FAILED"
        )
        result, _ = self._run(t)
        assert result is False

    def test_skip_result_contains_scope_guard(self):
        t = _ticket(result="pe_chain autonomous: SCOPE_GUARD trip on brainstem/core.py")
        result, _ = self._run(t)
        assert result is False

    def test_skip_result_contains_skipped(self):
        t = _ticket(result="pe_chain autonomous: commit skipped")
        result, _ = self._run(t)
        assert result is False

    def test_skip_worker_not_igor(self):
        t = _ticket(worker="claude")
        result, _ = self._run(t)
        assert result is False

    def test_skip_worker_none(self):
        t = _ticket(worker=None)
        result, _ = self._run(t)
        assert result is False

    def test_medium_size_passes(self):
        t = _ticket(size="M")
        result, _ = self._run(t)
        assert result is True
        assert t["status"] == "closed"

    def test_auto_validate_calls_rollup_and_ungate(self):
        t = _ticket(decision_id="D-foo")
        tasks = [t]
        patches = _mock_side_effects()
        mocks = [p.start() for p in patches]
        try:
            q._try_auto_validate(tasks, t)
            mock_rollup = mocks[1]
            mock_ungate = mocks[2]
            mock_rollup.assert_called_once_with(tasks, "D-foo")
            mock_ungate.assert_called_once_with(tasks, "T-test")
        finally:
            for p in patches:
                p.stop()

    def test_auto_validate_calls_prepend_and_slate(self):
        t = _ticket()
        patches = _mock_side_effects()
        mocks = [p.start() for p in patches]
        try:
            q._try_auto_validate([t], t)
            mock_prepend = mocks[4]
            mock_slate = mocks[5]
            mock_prepend.assert_called_once_with("T-test", t["title"])
            mock_slate.assert_called_once_with(t)
        finally:
            for p in patches:
                p.stop()
