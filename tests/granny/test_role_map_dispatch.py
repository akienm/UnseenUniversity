"""Tests for role-based worker dispatch (T-granny-dispatch-role-map).

Workers self-declare their role capabilities via register_worker(roles=[...]).
Granny matches tickets to available workers at dispatch time — no hardcoded mapping.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devices.granny.device import GrannyWeatherwaxDevice, WorkerNode


# ── helpers ───────────────────────────────────────────────────────────────────


def _ticket(role="master", worker="claude", size="S", tags=None):
    return {
        "id": "T-test",
        "title": "test ticket",
        "size": size,
        "role": role,
        "worker": worker,
        "tags": tags or ["Platform"],
        "status": "sprint",
        "description": (
            "Problem.\n"
            "**Affected files:** foo.py\n"
            "**Scope boundary:** just this\n"
            "**Completion criteria:** tests pass"
        ),
    }


def _make_worker(worker_id, roles, available=True, skip_audit=False):
    dispatch_fn = MagicMock(return_value=True)
    node = WorkerNode(
        worker_id=worker_id,
        handled_tags=[],
        dispatch_fn=dispatch_fn,
        roles=frozenset(roles),
        availability_fn=lambda: available,
        skip_audit=skip_audit,
    )
    return node


def _make_daemon_with_workers(workers: list[WorkerNode]):
    """Build a bare GrannyDaemon with a device that returns the given workers."""
    from devices.granny.daemon import GrannyDaemon

    daemon = GrannyDaemon.__new__(GrannyDaemon)
    daemon._dispatched_ids = set()
    daemon._alerted_ids = set()
    daemon._total_dispatched = 0
    daemon._total_errors = 0
    daemon._last_poll = None
    daemon._imap = MagicMock()

    audit = MagicMock()
    audit.passed = True
    audit.escalate_to_cc = False
    audit.reasons = []
    audit.warnings = []

    device = MagicMock()
    device.intake_ticket.return_value = audit

    def _get_workers_for_role(role):
        return [w for w in workers if role in w.roles]

    device.get_workers_for_role.side_effect = _get_workers_for_role
    daemon._device = device
    daemon._inference_dispatch = MagicMock(return_value=True)
    return daemon


# ── WorkerNode capability ─────────────────────────────────────────────────────


class TestWorkerNodeCapability:
    def test_roles_stored_as_frozenset(self):
        node = _make_worker("CC.0", ["master", "guru"])
        assert "master" in node.roles
        assert "guru" in node.roles

    def test_available_when_fn_returns_true(self):
        node = _make_worker("CC.0", ["master"], available=True)
        assert node.is_available() is True

    def test_unavailable_when_fn_returns_false(self):
        node = _make_worker("CC.0", ["master"], available=False)
        assert node.is_available() is False

    def test_always_available_when_no_fn(self):
        node = WorkerNode(worker_id="OR.0", handled_tags=[], roles=frozenset({"apprentice"}))
        assert node.is_available() is True


# ── register_worker with roles ────────────────────────────────────────────────


class TestRegisterWorkerRoles:
    def test_registered_worker_appears_for_declared_role(self):
        with patch.object(GrannyWeatherwaxDevice, "_post_to_channel"):
            device = GrannyWeatherwaxDevice()
        device.register_worker("CC.0", [], roles=["master", "guru"])
        workers = device.get_workers_for_role("master")
        assert len(workers) == 1
        assert workers[0].worker_id == "CC.0"

    def test_not_returned_for_undeclared_role(self):
        with patch.object(GrannyWeatherwaxDevice, "_post_to_channel"):
            device = GrannyWeatherwaxDevice()
        device.register_worker("DickSimnel.0", [], roles=["builder", "creator"])
        assert device.get_workers_for_role("master") == []

    def test_two_workers_different_roles(self):
        with patch.object(GrannyWeatherwaxDevice, "_post_to_channel"):
            device = GrannyWeatherwaxDevice()
        device.register_worker("CC.0", [], roles=["master", "guru"])
        device.register_worker("DickSimnel.0", [], roles=["builder", "creator"])
        assert len(device.get_workers_for_role("master")) == 1
        assert device.get_workers_for_role("master")[0].worker_id == "CC.0"
        assert len(device.get_workers_for_role("builder")) == 1
        assert device.get_workers_for_role("builder")[0].worker_id == "DickSimnel.0"


# ── role-based dispatch routing ───────────────────────────────────────────────


class TestRoleBasedDispatch:
    @pytest.fixture(autouse=True)
    def _no_live(self, monkeypatch):
        monkeypatch.setattr("devices.granny.daemon._cc0_available", lambda: False)
        monkeypatch.setattr("devices.granny.daemon._dicksimnel_available", lambda: False)

    def _run(self, daemon, ticket):
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=[ticket]),
            patch("devices.granny.daemon._check_rate_limit", return_value=(True, None, 0.0)),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
        ):
            return daemon.run_once()

    def test_master_ticket_dispatched_to_cc_worker(self):
        cc_worker = _make_worker("CC.0", ["master", "guru"], available=True, skip_audit=True)
        daemon = _make_daemon_with_workers([cc_worker])
        count = self._run(daemon, _ticket(role="master"))
        assert count == 1
        cc_worker.dispatch_fn.assert_called_once()

    def test_guru_ticket_dispatched_to_cc_worker(self):
        cc_worker = _make_worker("CC.0", ["master", "guru"], available=True, skip_audit=True)
        daemon = _make_daemon_with_workers([cc_worker])
        count = self._run(daemon, _ticket(role="guru"))
        assert count == 1
        cc_worker.dispatch_fn.assert_called_once()

    def test_builder_ticket_dispatched_to_ds_worker(self):
        ds_worker = _make_worker("DickSimnel.0", ["builder", "creator"], available=True)
        daemon = _make_daemon_with_workers([ds_worker])
        count = self._run(daemon, _ticket(role="builder", worker="dicksimnel"))
        assert count == 1
        ds_worker.dispatch_fn.assert_called_once()

    def test_creator_ticket_dispatched_to_ds_worker(self):
        ds_worker = _make_worker("DickSimnel.0", ["builder", "creator"], available=True)
        daemon = _make_daemon_with_workers([ds_worker])
        count = self._run(daemon, _ticket(role="creator"))
        assert count == 1
        ds_worker.dispatch_fn.assert_called_once()

    def test_master_ticket_defers_when_cc_unavailable(self):
        cc_worker = _make_worker("CC.0", ["master", "guru"], available=False, skip_audit=True)
        daemon = _make_daemon_with_workers([cc_worker])
        count = self._run(daemon, _ticket(role="master"))
        assert count == 0
        cc_worker.dispatch_fn.assert_not_called()

    def test_builder_ticket_defers_when_ds_unavailable(self):
        ds_worker = _make_worker("DickSimnel.0", ["builder", "creator"], available=False)
        daemon = _make_daemon_with_workers([ds_worker])
        count = self._run(daemon, _ticket(role="builder"))
        assert count == 0
        ds_worker.dispatch_fn.assert_not_called()
        daemon._inference_dispatch.assert_not_called()

    def test_builder_deferred_does_not_reach_or_cascade(self):
        """builder-role tickets must never fall through to OR cascade."""
        ds_worker = _make_worker("DickSimnel.0", ["builder", "creator"], available=False)
        daemon = _make_daemon_with_workers([ds_worker])
        self._run(daemon, _ticket(role="builder"))
        daemon._inference_dispatch.assert_not_called()

    def test_cc_worker_skips_audit(self):
        """CC.0 has skip_audit=True — intake_ticket should not be called."""
        cc_worker = _make_worker("CC.0", ["master"], available=True, skip_audit=True)
        daemon = _make_daemon_with_workers([cc_worker])
        self._run(daemon, _ticket(role="master"))
        daemon._device.intake_ticket.assert_not_called()

    def test_ds_worker_runs_audit_before_dispatch(self):
        """DickSimnel.0 has skip_audit=False — audit runs before dispatch."""
        ds_worker = _make_worker("DickSimnel.0", ["builder"], available=True, skip_audit=False)
        daemon = _make_daemon_with_workers([ds_worker])
        self._run(daemon, _ticket(role="builder"))
        daemon._device.intake_ticket.assert_called_once()
        ds_worker.dispatch_fn.assert_called_once()

    def test_high_inertia_ticket_held_even_for_ds_role(self):
        """HIGH-inertia tag escalates to CC regardless of role — DS does not dispatch."""
        ds_worker = _make_worker("DickSimnel.0", ["builder"], available=True)
        daemon = _make_daemon_with_workers([ds_worker])
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=True, reasons=["HIGH-inertia: shim.py"], warnings=[]
        )
        held = []
        daemon._hold_for_cc_approval = lambda tid, r: held.append(tid)
        count = self._run(daemon, _ticket(role="builder", tags=["RackContract"]))
        assert count == 1  # held counts as dispatched (status → hold)
        assert held == ["T-test"]
        ds_worker.dispatch_fn.assert_not_called()


# ── _infer_role helper ────────────────────────────────────────────────────────


class TestInferRole:
    def test_explicit_role_used_when_valid(self):
        from devices.granny.daemon import _infer_role
        assert _infer_role({"role": "creator", "worker": "dicksimnel"}) == "creator"

    def test_falls_back_to_worker_when_no_role(self):
        from devices.granny.daemon import _infer_role
        assert _infer_role({"role": "", "worker": "claude"}) == "master"

    def test_dicksimnel_worker_infers_builder(self):
        from devices.granny.daemon import _infer_role
        assert _infer_role({"worker": "dicksimnel"}) == "builder"

    def test_unknown_worker_defaults_to_apprentice(self):
        from devices.granny.daemon import _infer_role
        assert _infer_role({"worker": "mystery-bot"}) == "apprentice"
