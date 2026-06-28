"""Tests for C-clan-instance-scoping — inter-agent memory access protocol.

Verifies:
- MemoryAccessRequest serialises / deserialises correctly
- MemoryAccessResponse roundtrip
- A requesting agent gets a bus response, not a direct DB query
- default_approve policy: client-scope denied, agent-scope approved
"""
from __future__ import annotations

from unseen_university.devices.bus.memory_protocol import (
    MemoryAccessRequest,
    MemoryAccessResponse,
    MemoryRecord,
    default_approve,
)


class TestMemoryAccessRequest:
    def test_roundtrip(self):
        req = MemoryAccessRequest(
            from_agent="librarian-wild-0001",
            to_agent="igor-wild-0001",
            scope="agent",
            query="recent escalation summaries",
            intent="recall index population",
            max_results=5,
        )
        payload = req.to_payload()
        assert payload["kind"] == "memory.access_request"
        assert payload["from_agent"] == "librarian-wild-0001"
        assert payload["scope"] == "agent"
        assert payload["max_results"] == 5

        restored = MemoryAccessRequest.from_payload(payload)
        assert restored.from_agent == req.from_agent
        assert restored.to_agent == req.to_agent
        assert restored.query == req.query
        assert restored.max_results == req.max_results

    def test_default_kind(self):
        req = MemoryAccessRequest()
        assert req.kind == "memory.access_request"


class TestMemoryAccessResponse:
    def test_roundtrip_approved(self):
        mem = MemoryRecord(id="42.1234567890", narrative="Inference failed", memory_type="EPISODIC")
        resp = MemoryAccessResponse(
            request_id="env-uuid-123",
            from_agent="igor-wild-0001",
            to_agent="librarian-wild-0001",
            approved=True,
            memories=[mem],
        )
        payload = resp.to_payload()
        assert payload["approved"] is True
        assert len(payload["memories"]) == 1
        assert payload["memories"][0]["id"] == "42.1234567890"

        restored = MemoryAccessResponse.from_payload(payload)
        assert restored.approved is True
        assert len(restored.memories) == 1
        assert restored.memories[0].narrative == "Inference failed"

    def test_roundtrip_denied(self):
        resp = MemoryAccessResponse(
            request_id="env-uuid-456",
            from_agent="igor-wild-0001",
            to_agent="librarian-wild-0001",
            approved=False,
            denied_reason="client-scope memory is siloed; no cross-client reads",
        )
        payload = resp.to_payload()
        assert payload["approved"] is False
        assert payload["memories"] == []
        assert "siloed" in payload["denied_reason"]

        restored = MemoryAccessResponse.from_payload(payload)
        assert restored.approved is False
        assert restored.denied_reason is not None


class TestDefaultApprovePolicy:
    def test_agent_scope_approved(self):
        req = MemoryAccessRequest(scope="agent")
        approved, reason = default_approve(req)
        assert approved is True
        assert reason is None

    def test_clan_scope_approved(self):
        req = MemoryAccessRequest(scope="clan")
        approved, reason = default_approve(req)
        assert approved is True
        assert reason is None

    def test_client_scope_denied(self):
        req = MemoryAccessRequest(scope="client")
        approved, reason = default_approve(req)
        assert approved is False
        assert reason is not None
        assert "siloed" in reason

    def test_unknown_scope_denied(self):
        req = MemoryAccessRequest(scope="universal")
        approved, reason = default_approve(req)
        assert approved is False


class TestBusResponseNotDirectDBQuery:
    """Verify the protocol: a requesting agent gets a bus response, not a direct DB query.

    This test simulates an agent that needs memories from Igor. It constructs a
    MemoryAccessRequest, sends it as a bus payload, and verifies the response shape
    comes back via bus (MemoryAccessResponse), not via a direct DB connection.

    The 'no direct DB query' invariant: the requester never calls psycopg2 / _db_conn()
    directly. It sends a bus message and awaits a bus response.
    """

    def test_requester_uses_bus_not_db(self, monkeypatch):
        """Simulate a requesting agent that sends a bus request and reads the response."""
        import psycopg2

        db_calls = []
        original_connect = psycopg2.connect

        def _track_connect(*args, **kwargs):
            db_calls.append(("connect", args, kwargs))
            return original_connect(*args, **kwargs)

        # The requesting agent should NOT call psycopg2.connect — it sends a bus message.
        # We verify this by checking db_calls is empty after the request is constructed.
        monkeypatch.setattr(psycopg2, "connect", _track_connect)

        # Step 1: Requester constructs a bus request — no DB involved
        req = MemoryAccessRequest(
            from_agent="librarian-wild-0001",
            to_agent="igor-wild-0001",
            scope="agent",
            query="escalation summaries",
            intent="recall index",
        )
        payload = req.to_payload()

        # Step 2: Owning agent (Igor) receives the payload and approves
        restored_req = MemoryAccessRequest.from_payload(payload)
        approved, reason = default_approve(restored_req)

        # Step 3: Igor constructs a bus response (no DB in this unit test)
        memories = [MemoryRecord(id="1.1000", narrative="mock memory", memory_type="EPISODIC")]
        resp = MemoryAccessResponse(
            request_id="test-req-id",
            from_agent="igor-wild-0001",
            to_agent="librarian-wild-0001",
            approved=approved,
            memories=memories if approved else [],
            denied_reason=reason,
        )

        # Step 4: Requester reads the bus response payload
        resp_payload = resp.to_payload()
        received = MemoryAccessResponse.from_payload(resp_payload)

        assert received.approved is True
        assert len(received.memories) == 1
        assert received.memories[0].narrative == "mock memory"

        # The requesting agent never touched the DB
        assert db_calls == [], (
            f"Requesting agent must use bus, not DB directly. "
            f"Unexpected DB calls: {db_calls}"
        )
