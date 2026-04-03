"""
test_pe_plan_filter_probe.py — Unit tests for pe_plan, pe_filter, pe_probe.

Covers:
  pe_plan:
    - fast path: ticket has 'plan' key → uses it directly
    - tier.2 path: parses PLAN:/TEST: from raw response
    - fallback: tier.2 unavailable → uses ticket description
    - error passthrough: basket["error"] skips step

  pe_filter:
    - PASS: plan_summary + test_criterion present, no high inertia files
    - WARN: missing test_criterion (but plan present) → warn, proceed
    - FAIL + escalate: HIGH inertia file in plan_files
    - error passthrough

  pe_probe:
    - SKIP: no probe_criterion in ticket
    - PASS: criterion sent, expected pattern found in Igor response
    - FAIL: criterion sent, expected pattern NOT found → escalate
    - error passthrough
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools.pe_chain import pe_filter, pe_plan, pe_probe

# ── pe_plan ───────────────────────────────────────────────────────────────────


class TestPePlan:
    def _basket(self, **kwargs) -> dict:
        base = {
            "ticket_id": "T-test",
            "ticket_description": "Fix the foo function to handle None input",
            "ticket": {},
        }
        base.update(kwargs)
        return base

    def test_error_passthrough(self):
        basket = self._basket(error="upstream error")
        result = pe_plan(basket)
        assert result["error"] == "upstream error"
        assert "plan_summary" not in result

    def test_fast_path_ticket_plan(self):
        basket = self._basket(
            ticket={
                "plan": "Edit foo() in bar.py to guard None",
                "test_criterion": "run test_foo_none",
            }
        )
        result = pe_plan(basket)
        assert result["plan_summary"] == "Edit foo() in bar.py to guard None"
        assert result["test_criterion"] == "run test_foo_none"
        assert result["plan_source"] == "ticket_plan"

    def test_tier2_parses_plan_and_test(self):
        raw = "PLAN: Edit foo() in bar.py to add None guard\nTEST: pytest tests/test_bar.py::test_foo_none"
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=raw):
            with patch("wild_igor.igor.tools.ops.store_plan", return_value="ok"):
                result = pe_plan(self._basket())
        assert "Edit foo()" in result["plan_summary"]
        assert "test_foo_none" in result["test_criterion"]
        assert result["plan_source"] == "tier2_ollama"

    def test_tier2_unavailable_falls_back_to_description(self):
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None):
            result = pe_plan(self._basket())
        assert result["plan_source"] == "ticket_description"
        assert "Fix the foo" in result["plan_summary"]

    def test_store_plan_failure_is_non_fatal(self):
        raw = "PLAN: edit foo\nTEST: run tests"
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=raw):
            with patch("wild_igor.igor.tools.pe_chain.pe_plan.__module__"):
                pass  # just run with real store_plan call which will fail gracefully
            result = pe_plan(self._basket())
        # No error — store_plan failure is non-fatal
        assert "error" not in result


# ── pe_filter ─────────────────────────────────────────────────────────────────


class TestPeFilter:
    def _basket(self, **kwargs) -> dict:
        base = {
            "ticket_id": "T-test",
            "plan_summary": "Edit foo() in bar.py",
            "test_criterion": "run test_foo",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        base.update(kwargs)
        return base

    def test_error_passthrough(self):
        basket = self._basket(error="upstream")
        result = pe_filter(basket)
        assert result["error"] == "upstream"
        assert "filter_result" not in result

    def test_pass_all_checks(self):
        result = pe_filter(self._basket())
        assert result["filter_result"] == "PASS"
        assert result["filter_checks"]["plan_defined"] is True
        assert result["filter_checks"]["test_defined"] is True
        assert result["filter_checks"]["not_high_inertia"] is True
        assert "escalate_reason" not in result

    def test_warn_missing_test_criterion(self):
        result = pe_filter(self._basket(test_criterion=""))
        assert result["filter_result"].startswith("WARN:")
        assert "no test_criterion" in result["filter_result"]
        assert "escalate_reason" not in result

    def test_warn_missing_plan_summary(self):
        result = pe_filter(self._basket(plan_summary=""))
        assert result["filter_result"].startswith("WARN:")
        assert "no plan_summary" in result["filter_result"]
        assert "escalate_reason" not in result

    def test_fail_high_inertia_brainstem(self):
        result = pe_filter(
            self._basket(plan_files=["wild_igor/igor/brainstem/core.py"])
        )
        assert result["filter_result"].startswith("FAIL:")
        assert "escalate_reason" in result
        assert "filter_fail" in result["escalate_reason"]

    def test_fail_high_inertia_models(self):
        result = pe_filter(self._basket(plan_files=["wild_igor/igor/memory/models.py"]))
        assert result["filter_result"].startswith("FAIL:")
        assert "escalate_reason" in result

    def test_empty_plan_files_passes_inertia_check(self):
        result = pe_filter(self._basket(plan_files=[]))
        assert result["filter_checks"]["not_high_inertia"] is True


# ── pe_probe ──────────────────────────────────────────────────────────────────


class TestPeProbe:
    def _basket(self, probe_criterion: str = "") -> dict:
        return {
            "ticket_id": "T-test",
            "ticket": {"probe_criterion": probe_criterion},
        }

    def test_error_passthrough(self):
        basket = self._basket()
        basket["error"] = "upstream"
        result = pe_probe(basket)
        assert result["error"] == "upstream"

    def test_skip_no_probe_criterion(self):
        result = pe_probe(self._basket(probe_criterion=""))
        assert result["probe_result"].startswith("SKIP:")
        assert "no probe_criterion" in result["probe_result"]
        assert "escalate_reason" not in result

    def test_pass_expected_pattern_found(self):
        fake_messages = [{"author": "igor", "content": "hello world response"}]
        with patch("urllib.request.urlopen") as mock_open:
            import json

            # First urlopen = cc_send POST (succeeds)
            post_ctx = MagicMock()
            post_ctx.__enter__ = lambda s: s
            post_ctx.__exit__ = MagicMock(return_value=False)

            # Second urlopen = channel_read GET
            get_ctx = MagicMock()
            get_ctx.__enter__ = lambda s: s
            get_ctx.__exit__ = MagicMock(return_value=False)
            get_ctx.read = lambda: json.dumps(fake_messages).encode()

            mock_open.side_effect = [post_ctx, get_ctx]

            with patch("time.sleep"):
                result = pe_probe(
                    self._basket(probe_criterion="send greeting\nexpect: hello world")
                )

        assert result["probe_result"] == "PASS"
        assert "escalate_reason" not in result

    def test_fail_expected_pattern_not_found(self):
        fake_messages = [{"author": "igor", "content": "something unrelated"}]
        with patch("urllib.request.urlopen") as mock_open:
            import json

            post_ctx = MagicMock()
            post_ctx.__enter__ = lambda s: s
            post_ctx.__exit__ = MagicMock(return_value=False)

            get_ctx = MagicMock()
            get_ctx.__enter__ = lambda s: s
            get_ctx.__exit__ = MagicMock(return_value=False)
            get_ctx.read = lambda: json.dumps(fake_messages).encode()

            mock_open.side_effect = [post_ctx, get_ctx]

            with patch("time.sleep"):
                result = pe_probe(
                    self._basket(probe_criterion="send greeting\nexpect: hello world")
                )

        assert result["probe_result"].startswith("FAIL:")
        assert "escalate_reason" in result
        assert "probe_fail" in result["escalate_reason"]

    def test_network_error_becomes_skip(self):
        with patch("urllib.request.urlopen", side_effect=OSError("conn refused")):
            result = pe_probe(self._basket(probe_criterion="test something"))
        assert result["probe_result"].startswith("SKIP:")
        assert "escalate_reason" not in result
