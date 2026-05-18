"""Tests for devices/inference/budget_gate.py and budget_tools.py wiring."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

# ── budget_gate unit tests ────────────────────────────────────────────────────


class TestCheckBalance:
    def _fresh_cache(self):
        import devices.inference.budget_gate as bg

        bg._cache = {}

    def test_returns_ok_when_balance_positive(self):
        self._fresh_cache()
        fake = {
            "purchased": 100.0,
            "used": 50.0,
            "balance": 50.0,
            "fetched_at": time.time(),
        }
        with (
            patch(
                "devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("devices.inference.budget_gate._write_balance_history"),
        ):
            from devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert ok
        assert "50" in msg

    def test_returns_blocked_when_balance_zero(self):
        self._fresh_cache()
        fake = {
            "purchased": 100.0,
            "used": 100.0,
            "balance": 0.0,
            "fetched_at": time.time(),
        }
        with (
            patch(
                "devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("devices.inference.budget_gate._write_balance_history"),
        ):
            from devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert not ok
        assert "exhausted" in msg.lower()

    def test_fail_open_when_api_unreachable(self):
        self._fresh_cache()
        with patch(
            "devices.inference.budget_gate._fetch_balance_raw", return_value=None
        ):
            from devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert ok  # fail-open: don't block on network error

    def test_blocked_at_floor(self, monkeypatch):
        self._fresh_cache()
        monkeypatch.setenv("IGOR_CLOUD_BUDGET_FLOOR_USD", "5.0")
        fake = {
            "purchased": 100.0,
            "used": 96.0,
            "balance": 4.0,
            "fetched_at": time.time(),
        }
        with (
            patch(
                "devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("devices.inference.budget_gate._write_balance_history"),
        ):
            from devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert not ok
        assert "floor" in msg.lower()


class TestRecordSpend:
    def test_noop_without_db_url(self, monkeypatch):
        monkeypatch.delenv("IGOR_HOME_DB_URL", raising=False)
        # Should not raise even without psycopg2 available
        from devices.inference.budget_gate import record_spend

        record_spend("gpt-4o-mini", 100, 50)  # no exception

    def test_writes_to_infra_spend(self, monkeypatch):
        monkeypatch.setenv("IGOR_HOME_DB_URL", "postgresql://test/test")
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        with patch("psycopg2.connect", return_value=mock_conn):
            from devices.inference.budget_gate import record_spend

            record_spend("openai/gpt-4o-mini", 200, 80, caller="adc_inference")
        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "infra.spend" in sql
        assert "openai/gpt-4o-mini" in params
        assert "caller=adc_inference" in params[3]
        assert "in=200" in params[3]


# ── InferenceDevice.dispatch() integration ───────────────────────────────────


class TestDispatchBudgetGate:
    def test_dispatch_checks_balance_before_or_call(self):
        from devices.inference.device import InferenceDevice
        from devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])

        with patch(
            "devices.inference.budget_gate.check_balance",
            return_value=(False, "exhausted"),
        ) as mock_check:
            try:
                dev.dispatch(req)
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert "budget gate" in str(exc).lower()
            mock_check.assert_called_once()

    def test_dispatch_records_spend_after_or_call(self):
        from devices.inference.device import InferenceDevice
        from devices.inference.shim import InferenceRequest, InferenceResponse

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}], model="gpt-4o-mini"
        )

        fake_raw = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with (
            patch(
                "devices.inference.budget_gate.check_balance", return_value=(True, "OK")
            ),
            patch(
                "devices.inference.device.InferenceDevice._or_call",
                return_value=fake_raw,
            ),
            patch("devices.inference.budget_gate.record_spend") as mock_record,
        ):
            dev.dispatch(req)
        mock_record.assert_called_once_with("gpt-4o-mini", 10, 5)

    def test_dispatch_skips_budget_gate_for_ollama(self):
        from devices.inference.device import InferenceDevice
        from devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="ollama")
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])

        fake_raw = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "llama3",
            "usage": {},
        }

        with (
            patch("devices.inference.budget_gate.check_balance") as mock_check,
            patch(
                "devices.inference.device.InferenceDevice._ollama_call",
                return_value=fake_raw,
            ),
        ):
            dev.dispatch(req)
        mock_check.assert_not_called()


# ── budget_tools MCP wiring ───────────────────────────────────────────────────


class TestBudgetToolsSchemas:
    def test_schemas_registered(self):
        from agent_datacenter.devices.librarian.tools import budget_tools

        names = {s["name"] for s in budget_tools.SCHEMAS}
        assert "check_openrouter_balance" in names
        assert "openrouter_burn_rate" in names

    def test_schemas_in_librarian_tools_init(self):
        from agent_datacenter.devices.librarian import tools

        names = {s["name"] for s in tools.SCHEMAS}
        assert "check_openrouter_balance" in names
        assert "openrouter_burn_rate" in names

    def test_dispatch_routes_check_balance(self):
        from agent_datacenter.devices.librarian import tools

        with patch(
            "agent_datacenter.devices.librarian.tools.budget_tools._fetch_or_balance",
            return_value=None,
        ):
            result = tools.dispatch("check_openrouter_balance", {})
        assert result is not None
        assert "unavailable" in result.lower() or "balance" in result.lower()

    def test_dispatch_routes_burn_rate(self):
        from agent_datacenter.devices.librarian import tools

        with patch(
            "agent_datacenter.devices.librarian.tools.budget_tools._burn_trajectory",
            return_value={"trend": "no_data", "sample_count": 0, "note": "test"},
        ):
            result = tools.dispatch("openrouter_burn_rate", {"window_hours": 24})
        assert result is not None
        assert isinstance(result, str)

    def test_dispatch_unknown_returns_none_from_module(self):
        from agent_datacenter.devices.librarian.tools import budget_tools

        assert budget_tools.dispatch("not_a_budget_tool", {}) is None

    def test_burn_rate_graceful_no_db(self, monkeypatch):
        monkeypatch.delenv("IGOR_HOME_DB_URL", raising=False)
        from agent_datacenter.devices.librarian.tools.budget_tools import (
            _openrouter_burn_rate,
        )

        result = _openrouter_burn_rate(48.0)
        assert "insufficient" in result.lower() or "not set" in result.lower()
