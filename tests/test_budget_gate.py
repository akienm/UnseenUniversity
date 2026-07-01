"""Tests for devices/inference/budget_gate.py and budget_tools.py wiring."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.sources import SourceRegistry

# ── budget_gate unit tests ────────────────────────────────────────────────────


class TestCheckBalance:
    def _fresh_cache(self):
        import unseen_university.devices.inference.budget_gate as bg

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
                "unseen_university.devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("unseen_university.devices.inference.budget_gate._write_balance_history"),
        ):
            from unseen_university.devices.inference.budget_gate import check_balance

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
                "unseen_university.devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("unseen_university.devices.inference.budget_gate._write_balance_history"),
        ):
            from unseen_university.devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert not ok
        assert "exhausted" in msg.lower()

    def test_fail_open_when_api_unreachable(self):
        self._fresh_cache()
        with patch(
            "unseen_university.devices.inference.budget_gate._fetch_balance_raw", return_value=None
        ):
            from unseen_university.devices.inference.budget_gate import check_balance

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
                "unseen_university.devices.inference.budget_gate._fetch_balance_raw", return_value=fake
            ),
            patch("unseen_university.devices.inference.budget_gate._write_balance_history"),
        ):
            from unseen_university.devices.inference.budget_gate import check_balance

            ok, msg = check_balance()
        assert not ok
        assert "floor" in msg.lower()


class TestRecordSpend:
    def test_noop_without_db_url(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        # Should not raise even without psycopg2 available
        from unseen_university.devices.inference.budget_gate import record_spend

        record_spend("gpt-4o-mini", 100, 50)  # no exception

    def test_writes_to_infra_spend(self, monkeypatch):
        monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://test/test")
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        with patch("psycopg2.connect", return_value=mock_conn):
            from unseen_university.devices.inference.budget_gate import record_spend

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
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="openrouter")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini", pin_reason="inference_test",
        )

        with patch(
            "unseen_university.devices.inference.budget_gate.check_balance",
            return_value=(False, "exhausted"),
        ) as mock_check:
            try:
                dev.dispatch(req)
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert "budget gate" in str(exc).lower()
            mock_check.assert_called_once()

    def test_dispatch_records_spend_after_or_call(self):
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse

        dev = InferenceDevice(mode="openrouter", sources=SourceRegistry())
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}], model="gpt-4o-mini",
            pin_reason="inference_test",
        )

        fake_raw = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with (
            patch(
                "unseen_university.devices.inference.budget_gate.check_balance", return_value=(True, "OK")
            ),
            patch(
                "unseen_university.devices.inference.device.InferenceDevice._or_call",
                return_value=fake_raw,
            ),
            patch("unseen_university.devices.inference.budget_gate.record_spend") as mock_record,
        ):
            dev.dispatch(req)
        mock_record.assert_called_once_with("gpt-4o-mini", 10, 5)

    def test_dispatch_skips_budget_gate_for_ollama(self):
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        dev = InferenceDevice(mode="ollama", sources=SourceRegistry())
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])

        fake_raw = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "llama3",
            "usage": {},
        }

        with (
            patch("unseen_university.devices.inference.budget_gate.check_balance") as mock_check,
            patch(
                "unseen_university.devices.inference.device.InferenceDevice._ollama_call",
                return_value=fake_raw,
            ),
        ):
            dev.dispatch(req)
        mock_check.assert_not_called()


# ── alert tests ──────────────────────────────────────────────────────────────


class TestMaybeAlert:
    def _clear_stamp(self):
        import unseen_university.devices.inference.budget_gate as bg

        bg._ALERT_STAMP.unlink(missing_ok=True)

    def test_no_alert_above_threshold(self, monkeypatch):
        self._clear_stamp()
        monkeypatch.setenv("OR_BUDGET_ALERT_USD", "15.0")
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.inference.budget_gate import _maybe_alert

        _maybe_alert(20.0)  # above threshold — no alert, no exception

    def test_alert_fires_below_threshold(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OR_BUDGET_ALERT_USD", "15.0")
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        import unseen_university.devices.inference.budget_gate as bg

        bg._ALERT_STAMP = tmp_path / "stamp"
        from unseen_university.devices.inference.budget_gate import _maybe_alert

        _maybe_alert(10.0)  # below threshold
        assert bg._ALERT_STAMP.exists()

    def test_alert_deduped_within_window(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OR_BUDGET_ALERT_USD", "15.0")
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        import unseen_university.devices.inference.budget_gate as bg

        stamp = tmp_path / "stamp"
        stamp.touch()  # pretend we already alerted just now
        bg._ALERT_STAMP = stamp
        # Patch stat to return recent mtime
        original_stat = stamp.stat

        import time

        with patch("unseen_university.devices.inference.budget_gate._ALERT_DEDUP_SECS", 9999):
            # stamp exists and is "recent" — alert should be suppressed
            called = []
            original_log_warning = bg.log.warning
            bg.log.warning = lambda *a, **kw: called.append(a)
            try:
                from unseen_university.devices.inference.budget_gate import _maybe_alert

                _maybe_alert(5.0)
            finally:
                bg.log.warning = original_log_warning
        assert not called, "alert should be deduped"


# ── budget_tools MCP wiring ───────────────────────────────────────────────────


class TestBudgetToolsSchemas:
    def test_schemas_registered(self):
        from unseen_university.devices.librarian.tools import budget_tools

        names = {s["name"] for s in budget_tools.SCHEMAS}
        assert "check_openrouter_balance" in names
        assert "openrouter_burn_rate" in names

    def test_schemas_in_librarian_tools_init(self):
        from unseen_university.devices.librarian import tools

        names = {s["name"] for s in tools.SCHEMAS}
        assert "check_openrouter_balance" in names
        assert "openrouter_burn_rate" in names

    def test_dispatch_routes_check_balance(self):
        from unseen_university.devices.librarian import tools

        with patch(
            "unseen_university.devices.librarian.tools.budget_tools._fetch_or_balance",
            return_value=None,
        ):
            result = tools.dispatch("check_openrouter_balance", {})
        assert result is not None
        assert "unavailable" in result.lower() or "balance" in result.lower()

    def test_dispatch_routes_burn_rate(self):
        from unseen_university.devices.librarian import tools

        with patch(
            "unseen_university.devices.librarian.tools.budget_tools._burn_trajectory",
            return_value={"trend": "no_data", "sample_count": 0, "note": "test"},
        ):
            result = tools.dispatch("openrouter_burn_rate", {"window_hours": 24})
        assert result is not None
        assert isinstance(result, str)

    def test_dispatch_unknown_returns_none_from_module(self):
        from unseen_university.devices.librarian.tools import budget_tools

        assert budget_tools.dispatch("not_a_budget_tool", {}) is None

    def test_burn_rate_graceful_no_db(self, monkeypatch):
        monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
        from unseen_university.devices.librarian.tools.budget_tools import (
            _openrouter_burn_rate,
        )

        result = _openrouter_burn_rate(48.0)
        assert "insufficient" in result.lower() or "not set" in result.lower()
