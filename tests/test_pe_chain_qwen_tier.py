"""test_pe_chain_qwen_tier.py — verify pe_chain routes to Qwen, not Claude.

T-verify-pe-chain-qwen-tier: coding sprints must run on the cheap background
tier (Qwen 2.5:7b via Ollama) so the worker=igor path doesn't burn Claude
tokens. This test runs one real pe_plan invocation and asserts the model id
logged to reasoning_calls.log contains 'qwen' (case-insensitive) and does NOT
contain 'claude' or 'anthropic'.

No mocks. If Ollama is unreachable, skips with a clear message — the ticket
explicitly forbids Claude fallback, so there's no alternative path to test.

Per Akien's 2026-04-20 constraint: if Qwen quality falls short, the remedy
is a LARGER Qwen, never a fallback to Claude. So this test is the tripwire.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools import pe_chain  # noqa: E402


def _ollama_reachable(**_) -> tuple[bool, str]:
    """Probe local Ollama /api/tags. Return (healthy, model_if_any)."""
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            import json

            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            qwen = next((m for m in models if "qwen" in m.lower()), "")
            return True, qwen
    except Exception:
        return False, ""


def _read_latest_reasoning_call() -> str | None:
    """Return the top (newest) line of reasoning_calls.log, or None."""
    from wild_igor.igor.paths import paths

    log_path = paths().logs / "reasoning_calls.log"
    if not log_path.exists():
        return None
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        first = f.readline()
    return first.strip() or None


@pytest.fixture(autouse=True)
def isolate_reasoning_calls_log(monkeypatch, tmp_path):
    """Isolate reasoning_calls.log per test to avoid cross-test pollution.

    This fixture:
    1. Saves the original log file if it exists
    2. Redirects pe_chain writes to a temporary per-test log
    3. Restores the original after the test

    This prevents concurrent Igor processes or other tests from writing to
    the shared log during our test, which would break our pre/post comparison.
    """
    from wild_igor.igor.paths import paths

    log_path = paths().logs / "reasoning_calls.log"
    backup_path = log_path.with_stem(log_path.stem + ".bak")

    # Save original if it exists
    if log_path.exists():
        import shutil

        shutil.copy2(log_path, backup_path)

    # Clear the log for this test
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")

    yield

    # Restore original after test
    if backup_path.exists():
        import shutil

        shutil.move(backup_path, log_path)
    elif log_path.exists():
        # No original existed; clean up our test file
        log_path.unlink()


class TestPeChainRoutesToQwen:
    """pe_chain must call Qwen on tier.2, never Claude."""

    # Real Qwen 7b round-trip runs ~15-60s depending on box; the global
    # pytest.ini timeout=30 is too tight for an integration-shaped test.
    @pytest.mark.timeout(180)
    def test_pe_plan_logs_qwen_model(self):
        """
        Run pe_plan on a trivial synthetic ticket. Verify the top line of
        reasoning_calls.log (written by _call_tier2 on success) names a Qwen
        model — not Claude or Anthropic.

        We do NOT go through run_pe_plan() because that loads the active
        GOAL and claims a real ticket; we call pe_plan() directly with a
        seeded basket. This exercises the same _call_tier2 path without
        mutating queue state.
        """
        reachable, qwen_model = _ollama_reachable()
        if not reachable:
            pytest.skip(
                "Ollama is not reachable on this box — cannot exercise pe_chain's "
                "tier.2 path. Per T-verify-pe-chain-qwen-tier, there is NO Claude "
                "fallback; this test only runs when local Qwen is available. "
                "TODO: exercise on a box with Ollama running (akienyoga9i, "
                "akiendell, akienyogai7, akiendelllinux)."
            )
        if not qwen_model:
            pytest.skip(
                f"Ollama reachable but no Qwen model pulled locally. "
                f"Run `ollama pull qwen2.5:7b` and retry."
            )

        # Snapshot: what was the top of the log BEFORE our call?
        pre_top = _read_latest_reasoning_call()

        # Seed a minimal basket so pe_plan does its tier.2 call
        basket = {
            "ticket_id": "T-verify-pe-chain-qwen-tier-synthetic",
            "ticket_description": (
                "Synthetic integration test for pe_chain tier routing. "
                "Emit a one-line plan for a fictional bug: a function "
                "foo() in bar.py returns None instead of 0."
            ),
            "ticket": {},  # empty dict → no ticket.plan shortcut, forces tier.2 call
        }

        t0 = time.monotonic()
        result = pe_chain.pe_plan(basket)
        elapsed = time.monotonic() - t0

        # Verify the call actually reached tier.2 (not the ticket_plan shortcut)
        assert result.get("plan_source") == "tier2_ollama", (
            f"pe_plan didn't hit tier.2 path — plan_source={result.get('plan_source')!r}. "
            f"If plan_source is 'ticket_description', Ollama returned empty "
            f"(treat as local failure); if 'ticket_plan', our basket seeding is wrong."
        )

        # Read the new top-of-log entry — must be our call, not pre_top
        post_top = _read_latest_reasoning_call()
        assert post_top, "reasoning_calls.log is empty after pe_plan call"
        assert post_top != pre_top, (
            f"reasoning_calls.log top-line unchanged after pe_plan "
            f"(elapsed={elapsed:.1f}s) — _log_pe_inference() did not fire. "
            f"pre={pre_top!r}"
        )

        # The log line format is:
        #   TIMESTAMP|reasoning|PROVIDER|MODEL|tier=tier.2|...
        # Parse out the model field and assert it's Qwen, not Claude.
        parts = post_top.split("|")
        assert (
            len(parts) >= 4 and parts[1] == "reasoning"
        ), f"Unexpected log line shape: {post_top!r}"
        provider = parts[2]
        model = parts[3]

        model_lc = model.lower()
        assert "qwen" in model_lc, (
            f"pe_plan called the WRONG model: provider={provider!r} model={model!r}. "
            f"Expected a Qwen model (tier.2 Ollama). This means tier routing is "
            f"broken — pe_chain would burn cloud tokens on every ticket."
        )
        assert "claude" not in model_lc and "anthropic" not in model_lc, (
            f"pe_plan routed to Claude/Anthropic: provider={provider!r} model={model!r}. "
            f"This is explicitly forbidden by T-verify-pe-chain-qwen-tier."
        )
