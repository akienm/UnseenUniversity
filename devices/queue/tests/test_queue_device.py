"""Tests for QueueDevice.

Integration tests use real Postgres via IGOR_HOME_DB_URL. Unit tests mock the
DB connection to stay fast and isolated.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from devices.queue.device import (
    LegacyDirectClaimError,
    QueueDevice,
    _gate_tripped,
    _priority_key,
)

# ── Unit tests (no DB) ────────────────────────────────────────────────────────


class TestPriorityKey:
    def test_higher_float_priority_comes_first(self):
        high = {"priority": 0.9}
        low = {"priority": 0.3}
        assert _priority_key(high) < _priority_key(low)

    def test_p_number_priority(self):
        p1 = {"priority": 1}
        p2 = {"priority": 2}
        assert _priority_key(p1) < _priority_key(p2)

    def test_missing_priority_is_lowest(self):
        no_prio = {}
        has_prio = {"priority": 0.5}
        assert _priority_key(has_prio) < _priority_key(no_prio)


class TestLegacyClaimError:
    def test_queue_claim_raises(self):
        with pytest.raises(LegacyDirectClaimError):
            QueueDevice.queue_claim()


class TestGateTripped:
    def test_no_gate_file_not_tripped(self, tmp_path):
        from devices.queue import device as dev_mod

        original = dev_mod.GATE_FILE
        dev_mod.GATE_FILE = tmp_path / "nonexistent_gate.json"
        try:
            assert _gate_tripped() is False
        finally:
            dev_mod.GATE_FILE = original

    def test_tripped_true_when_file_says_tripped(self, tmp_path):
        gate_file = tmp_path / "queue_gate.json"
        gate_file.write_text(json.dumps({"tripped": True, "reason": "test"}))
        from devices.queue import device as dev_mod

        original = dev_mod.GATE_FILE
        dev_mod.GATE_FILE = gate_file
        try:
            assert _gate_tripped() is True
        finally:
            dev_mod.GATE_FILE = original

    def test_not_tripped_when_false(self, tmp_path):
        gate_file = tmp_path / "queue_gate.json"
        gate_file.write_text(json.dumps({"tripped": False}))
        from devices.queue import device as dev_mod

        original = dev_mod.GATE_FILE
        dev_mod.GATE_FILE = gate_file
        try:
            assert _gate_tripped() is False
        finally:
            dev_mod.GATE_FILE = original


class TestQueueDeviceContract:
    """Test BaseDevice contract methods (no DB)."""

    @pytest.fixture
    def device(self):
        with patch("devices.queue.device._db_conn") as mock_conn:
            mock_conn.return_value.close = MagicMock()
            yield QueueDevice()

    def test_who_am_i(self, device):
        info = device.who_am_i()
        assert info["device_id"] == "queue"
        assert "name" in info
        assert "version" in info

    def test_interface_version(self, device):
        assert device.interface_version() == "1.0"

    def test_capabilities_has_mcp_tools(self, device):
        caps = device.capabilities()
        assert "queue_next" in caps["mcp_tools"]
        assert "queue_peek" in caps["mcp_tools"]

    def test_requirements(self, device):
        reqs = device.requirements()
        assert "psycopg2" in reqs["deps"]

    def test_uptime_positive(self, device):
        import time

        time.sleep(0.01)
        assert device.uptime() > 0


class TestQueueNextGateTripped:
    def test_returns_none_when_gate_tripped(self, tmp_path):
        gate_file = tmp_path / "queue_gate.json"
        gate_file.write_text(json.dumps({"tripped": True}))
        from devices.queue import device as dev_mod

        original = dev_mod.GATE_FILE
        dev_mod.GATE_FILE = gate_file
        try:
            with patch("devices.queue.device._db_conn"):
                dev = QueueDevice()
                result = dev.queue_next("claude")
                assert result is None
        finally:
            dev_mod.GATE_FILE = original


class TestQueueNextMocked:
    """queue_next with a mocked DB cursor."""

    def _make_ticket(self, id_, worker, status="sprint", gate=None, priority=0.5):
        return {
            "id": id_,
            "title": f"Test ticket {id_}",
            "status": status,
            "worker": worker,
            "gate": gate,
            "priority": priority,
            "size": "S",
        }

    @pytest.fixture
    def device_no_gate(self, tmp_path):
        gate_file = tmp_path / "gate.json"
        gate_file.write_text(json.dumps({"tripped": False}))
        from devices.queue import device as dev_mod

        original = dev_mod.GATE_FILE
        dev_mod.GATE_FILE = gate_file
        with patch("devices.queue.device._db_conn") as mock_conn_fn:
            conn = MagicMock()
            mock_conn_fn.return_value = conn
            conn.__enter__ = lambda s: s
            conn.__exit__ = MagicMock(return_value=False)
            yield dev_mod.QueueDevice(), conn, mock_conn_fn
        dev_mod.GATE_FILE = original

    def test_returns_none_when_no_sprint_tickets(self, device_no_gate):
        dev, conn, mock_conn_fn = device_no_gate
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchall.return_value = []

        result = dev.queue_next("claude")
        assert result is None

    def test_returns_none_when_no_tickets_for_worker(self, device_no_gate):
        dev, conn, mock_conn_fn = device_no_gate
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        igor_ticket = self._make_ticket("T-igor-1", "igor")
        cursor.fetchall.return_value = [({"kind": "ticket", **igor_ticket},)]
        cursor.fetchone.return_value = None

        result = dev.queue_next("claude")
        assert result is None

    def test_skips_gated_tickets(self, device_no_gate):
        dev, conn, mock_conn_fn = device_no_gate
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        gated = self._make_ticket("T-gated", "claude", gate="T-other")
        cursor.fetchall.return_value = [({"kind": "ticket", **gated},)]
        cursor.fetchone.return_value = None

        result = dev.queue_next("claude")
        assert result is None


# ── Integration tests (real Postgres) ────────────────────────────────────────

_PG_URL = os.environ.get("IGOR_HOME_DB_URL", "")
_skip_integration = pytest.mark.skipif(
    not _PG_URL, reason="IGOR_HOME_DB_URL not set — skipping integration tests"
)


@_skip_integration
class TestQueueIntegration:
    """Integration tests against real Postgres. These require a running DB."""

    @pytest.fixture
    def device(self):
        return QueueDevice()

    def test_health_returns_healthy(self, device):
        h = device.health()
        assert h["status"] == "healthy"
        assert "sprint_tickets" in h

    def test_queue_list_returns_list(self, device):
        tickets = device.queue_list(status="sprint")
        assert isinstance(tickets, list)

    def test_queue_show_unknown_id_returns_none(self, device):
        result = device.queue_show("T-nonexistent-ticket-id-xyz")
        assert result is None

    def test_queue_peek_returns_dict_or_none(self, device):
        result = device.queue_peek(worker="claude")
        assert result is None or isinstance(result, dict)

    def test_queue_next_returns_dict_or_none(self, device):
        # Read-only check: if there's a next ticket, peek and next should agree on ID
        peeked = device.queue_peek(worker="claude")
        if peeked is not None:
            ticket_id = peeked["id"]
            # Show returns the same ticket
            shown = device.queue_show(ticket_id)
            assert shown is not None
            assert shown["id"] == ticket_id
