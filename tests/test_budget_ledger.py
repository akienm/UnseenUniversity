"""
Integration tests for devices/inference/budget_ledger.py.

Uses the real Postgres test schema (test_clan_<ts>) created by tests/igor/conftest.py.
Requires UU_HOME_DB_URL; skipped gracefully when DB is absent.

Completion criteria verified:
  - After 3 inference calls, budget_summary(agent_id='igor') returns correct token
    counts grouped by session.
  - budget_remaining returns correct value against a set limit.
  - OR provider call rejected when limit exceeded (via check_session_limit).
  - max/ollama calls proceed without limit check (check_session_limit returns ok).
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest


def _db_connectable() -> bool:
    """Return True only when UU_HOME_DB_URL is set AND a connection actually succeeds."""
    db_url = os.environ.get("UU_HOME_DB_URL", "")
    if not db_url:
        return False
    try:
        import psycopg2

        conn = psycopg2.connect(db_url, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


DB_AVAILABLE = bool(os.environ.get("UU_HOME_DB_URL"))


@pytest.fixture
def live_db():
    """Skip this test when the DB is not reachable at execution time."""
    if not _db_connectable():
        pytest.skip("DB not connectable (pool exhausted or unavailable)")


# ── helpers ───────────────────────────────────────────────────────────────────


def _debit(agent_id, session_id, provider, cost_usd, tokens=(10, 5), model="gpt-4o"):
    from unseen_university.devices.inference.budget_ledger import debit

    debit(
        agent_id=agent_id,
        instance_id="wild-0001",
        coa_id="coa-1",
        session_id=session_id,
        provider=provider,
        model=model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cost_usd=cost_usd,
    )


# ── no-op behaviour when DB absent ───────────────────────────────────────────


class TestNoDB:
    def test_debit_noop(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        _debit("igor", "s1", "openrouter", 0.001)  # must not raise

    def test_budget_summary_empty(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_ledger import budget_summary

        assert budget_summary("igor") == []

    def test_budget_limit_set_false(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_ledger import budget_limit_set

        assert budget_limit_set("igor", "session", 5.0) is False

    def test_budget_remaining_safe_dict(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_ledger import budget_remaining

        r = budget_remaining("igor", "s1")
        assert r["remaining_usd"] is None

    def test_check_session_limit_noop_for_ollama(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_ledger import check_session_limit

        ok, _ = check_session_limit("igor", "s1", "ollama")
        assert ok

    def test_check_session_limit_failopen_for_or(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_ledger import check_session_limit

        ok, _ = check_session_limit("igor", "s1", "openrouter")
        assert ok  # fail-open when DB unavailable


# ── validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_budget_summary_bad_group_by(self):
        from unseen_university.devices.inference.budget_ledger import budget_summary

        with pytest.raises(ValueError):
            budget_summary("igor", group_by="bad_value")

    def test_budget_limit_set_bad_scope(self):
        from unseen_university.devices.inference.budget_ledger import budget_limit_set

        with pytest.raises(ValueError):
            budget_limit_set("igor", "bad_scope", 5.0)


# ── integration: real DB ──────────────────────────────────────────────────────


@pytest.mark.skipif(not DB_AVAILABLE, reason="UU_HOME_DB_URL not set")
class TestLedgerIntegration:
    """Real SQL against clan schema. Skips per-test when DB pool is exhausted."""

    pytestmark = pytest.mark.usefixtures("live_db")

    def _unique_agent(self) -> str:
        return f"test_agent_{int(time.time() * 1000) % 1_000_000}"

    def test_debit_and_summary_by_session(self):
        """After 3 inference calls, budget_summary returns correct token counts."""
        from unseen_university.devices.inference.budget_ledger import budget_summary

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        _debit(agent, session, "openrouter", 0.01, tokens=(100, 50))
        _debit(agent, session, "openrouter", 0.02, tokens=(200, 80))
        _debit(agent, session, "openrouter", 0.03, tokens=(300, 120))

        rows = budget_summary(agent, group_by="session")
        assert len(rows) == 1
        r = rows[0]
        assert r["group_key"] == session
        assert r["input_tokens"] == 600
        assert r["output_tokens"] == 250
        assert r["call_count"] == 3
        assert abs(r["cost_usd_total"] - 0.06) < 1e-6

    def test_debit_and_summary_multiple_sessions(self):
        from unseen_university.devices.inference.budget_ledger import budget_summary

        agent = self._unique_agent()
        sess_a = f"sess_a_{int(time.time() * 1000)}"
        sess_b = f"sess_b_{int(time.time() * 1000)}"

        _debit(agent, sess_a, "openrouter", 0.01, tokens=(10, 5))
        _debit(agent, sess_b, "openrouter", 0.02, tokens=(20, 10))

        rows = budget_summary(agent, group_by="session")
        keys = {r["group_key"] for r in rows}
        assert sess_a in keys and sess_b in keys

    def test_ollama_cost_null_excluded_from_sum(self):
        """Ollama calls (cost_usd=None) don't inflate the OR cost total."""
        from unseen_university.devices.inference.budget_ledger import budget_summary

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        _debit(agent, session, "openrouter", 0.01, tokens=(10, 5))
        _debit(agent, session, "ollama", None, tokens=(50, 30))  # tracking-only

        rows = budget_summary(agent, group_by="session")
        assert len(rows) == 1
        r = rows[0]
        assert r["call_count"] == 2
        assert abs(r["cost_usd_total"] - 0.01) < 1e-6  # ollama not included

    def test_budget_remaining_with_limit(self):
        """budget_remaining returns correct value against a set limit."""
        from unseen_university.devices.inference.budget_ledger import budget_limit_set, budget_remaining

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        budget_limit_set(agent, "session", 0.05)
        _debit(agent, session, "openrouter", 0.02, tokens=(10, 5))

        info = budget_remaining(agent, session)
        assert info["limit_usd"] == pytest.approx(0.05)
        assert info["spent_usd"] == pytest.approx(0.02)
        assert info["remaining_usd"] == pytest.approx(0.03)
        assert info["pct_used"] == pytest.approx(40.0)

    def test_budget_remaining_no_limit(self):
        from unseen_university.devices.inference.budget_ledger import budget_remaining

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        _debit(agent, session, "openrouter", 0.01, tokens=(10, 5))

        info = budget_remaining(agent, session)
        assert info["remaining_usd"] is None
        assert info["limit_usd"] is None
        assert info["spent_usd"] == pytest.approx(0.01)

    def test_or_enforcement_blocks_when_limit_exceeded(self):
        """OR call rejected when cumulative session spend >= limit."""
        from unseen_university.devices.inference.budget_ledger import (
            budget_limit_set,
            check_session_limit,
        )

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        budget_limit_set(agent, "session", 0.02)
        # Spend up to the limit
        _debit(agent, session, "openrouter", 0.01, tokens=(10, 5))
        _debit(agent, session, "openrouter", 0.01, tokens=(10, 5))

        # Now at limit — next OR call should be rejected
        ok, msg = check_session_limit(agent, session, "openrouter")
        assert not ok
        assert "exhausted" in msg.lower()

    def test_ollama_not_enforced_even_with_limit(self):
        """Ollama calls always return ok regardless of limit."""
        from unseen_university.devices.inference.budget_ledger import (
            budget_limit_set,
            check_session_limit,
        )

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        budget_limit_set(agent, "session", 0.001)
        _debit(agent, session, "openrouter", 0.01, tokens=(10, 5))

        ok, msg = check_session_limit(agent, session, "ollama")
        assert ok
        assert "tracking-only" in msg

    def test_no_limit_check_without_agent_session(self):
        """check_session_limit with no session limit set returns ok."""
        from unseen_university.devices.inference.budget_ledger import check_session_limit

        agent = self._unique_agent()
        ok, msg = check_session_limit(agent, "no-session", "openrouter")
        assert ok
        assert "no session limit" in msg

    def test_budget_limit_set_upserts(self):
        """budget_limit_set overwrites an existing limit."""
        from unseen_university.devices.inference.budget_ledger import budget_limit_set, budget_remaining

        agent = self._unique_agent()
        session = f"sess_{int(time.time() * 1000)}"

        budget_limit_set(agent, "session", 1.00)
        budget_limit_set(agent, "session", 2.00)  # update

        info = budget_remaining(agent, session)
        assert info["limit_usd"] == pytest.approx(2.00)


# ── dispatch integration: device.py debit hook ────────────────────────────────


class TestDispatchDebitHook:
    """Unit tests verifying device.py calls debit after dispatch."""

    def _fake_or_response(self, cost: float = 0.01) -> dict:
        return {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost},
        }

    def test_dispatch_debits_or_call(self):
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            agent_id="igor",
            session_id="sess-1",
        )

        with (
            patch(
                "unseen_university.devices.inference.budget_gate.check_balance", return_value=(True, "OK")
            ),
            patch(
                "unseen_university.devices.inference.budget_gate.record_spend",
            ),
            patch(
                "unseen_university.devices.inference.sources.OpenRouterSource.call",
                return_value=self._fake_or_response(0.01),
            ),
            patch(
                "unseen_university.devices.inference.budget_ledger.check_session_limit",
                return_value=(True, "ok"),
            ),
            patch("unseen_university.devices.inference.budget_ledger.debit") as mock_debit,
        ):
            dev.dispatch(req)

        mock_debit.assert_called_once()
        call_kwargs = mock_debit.call_args[1]
        assert call_kwargs["agent_id"] == "igor"
        assert call_kwargs["session_id"] == "sess-1"
        assert call_kwargs["provider"] == "openrouter"
        assert call_kwargs["cost_usd"] == pytest.approx(0.01)

    def test_dispatch_debits_ollama_with_null_cost(self):
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="ollama", endpoint="http://127.0.0.1:11434")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            agent_id="igor",
            session_id="sess-2",
        )
        fake = {
            "message": {"content": "pong"},
            "done": True,
            "done_reason": "stop",
            "model": "llama3",
            "prompt_eval_count": 8,
            "eval_count": 4,
        }
        with (
            patch(
                "unseen_university.devices.inference.rules_engine.RulesEngine.route",
                return_value=None,
            ),
            patch(
                "unseen_university.devices.inference.sources.OllamaSource.call",
                return_value=fake,
            ),
            patch("unseen_university.devices.inference.budget_ledger.debit") as mock_debit,
        ):
            dev.dispatch(req)

        mock_debit.assert_called_once()
        assert mock_debit.call_args[1]["cost_usd"] is None
        assert mock_debit.call_args[1]["provider"] == "ollama"

    def test_dispatch_enforces_or_limit(self):
        """dispatch() raises when check_session_limit returns not-ok."""
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            agent_id="igor",
            session_id="sess-3",
        )

        with (
            patch(
                "unseen_university.devices.inference.budget_gate.check_balance", return_value=(True, "OK")
            ),
            patch(
                "unseen_university.devices.inference.budget_ledger.check_session_limit",
                return_value=(
                    False,
                    "session budget exhausted: $0.0500 spent >= $0.0500 limit",
                ),
            ),
        ):
            with pytest.raises(RuntimeError, match="budget limit"):
                dev.dispatch(req)

    def test_dispatch_skips_limit_check_when_no_agent_id(self):
        """Requests without agent_id bypass the session limit check."""
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])

        with (
            patch(
                "unseen_university.devices.inference.budget_gate.check_balance", return_value=(True, "OK")
            ),
            patch("unseen_university.devices.inference.budget_gate.record_spend"),
            patch(
                "unseen_university.devices.inference.sources.OpenRouterSource.call",
                return_value=self._fake_or_response(0.0),
            ),
            patch("unseen_university.devices.inference.budget_ledger.check_session_limit") as mock_check,
            patch("unseen_university.devices.inference.budget_ledger.debit"),
        ):
            dev.dispatch(req)

        mock_check.assert_not_called()


# ── MCP tools in librarian ────────────────────────────────────────────────────


class TestBudgetLedgerMCPSchemas:
    def test_schemas_registered(self):
        from unseen_university.devices.librarian.tools import budget_tools

        names = {s["name"] for s in budget_tools.SCHEMAS}
        assert "budget_summary" in names
        assert "budget_limit_set" in names
        assert "budget_remaining" in names

    def test_schemas_in_librarian_init(self):
        from unseen_university.devices.librarian import tools

        names = {s["name"] for s in tools.SCHEMAS}
        assert "budget_summary" in names
        assert "budget_limit_set" in names
        assert "budget_remaining" in names

    def test_dispatch_budget_summary_no_db(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.librarian import tools

        result = tools.dispatch("budget_summary", {"agent_id": "igor"})
        assert result is not None
        assert "No ledger entries" in result or "error" in result.lower()

    def test_dispatch_budget_remaining_no_db(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.librarian import tools

        result = tools.dispatch(
            "budget_remaining", {"agent_id": "igor", "session_id": "s1"}
        )
        assert result is not None
        assert "No session limit" in result or "error" in result.lower()

    def test_dispatch_budget_limit_set_no_db(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.librarian import tools

        result = tools.dispatch(
            "budget_limit_set",
            {"agent_id": "igor", "scope": "session", "limit_usd": 5.0},
        )
        assert result is not None
        assert "unavailable" in result.lower() or "Failed" in result
