"""
Context-discipline tests for the shared AgenticLoop (T-agentic-loop-context-discipline).

The loop re-sends the WHOLE message history every turn. Without a bound, 3000-char tool
dumps accumulate linearly in turns until the prompt overflows num_ctx and a small model
drowns (2026-07-03 DS.0 observe-run: 2908 → ~32000 input_tokens over 38 turns → timeout
cliff at turn 39). These tests pin that:

  1. the re-sent history stays under a FIXED char bound no matter how many turns run
     (measured on the real payload handed to dispatch, not a mocked token count); and
  2. compaction never produces an API-invalid message sequence — the retained list is
     always the task message plus COMPLETE assistant→tool-result groups, so a real
     endpoint won't 400 on an orphaned tool result or a dangling assistant tool_call.

No live source: the InferenceDevice is a mock whose dispatch records the real request
messages and hands back a fresh Read tool call each turn, so the loop runs to max_turns.
"""

from __future__ import annotations

import json

from unittest.mock import MagicMock

from unseen_university.agentic.loop import AgenticLoop, NativeToolCodec


def _read_response(path: str, call_seq: int) -> MagicMock:
    """A native response that calls Read on `path` — never terminal, so the loop grinds on."""
    r = MagicMock()
    r.text = ""
    r.tool_calls = [{
        "id": f"call_{call_seq}",
        "type": "function",
        "function": {"name": "Read", "arguments": json.dumps({"path": path})},
    }]
    r.input_tokens = 50
    r.output_tokens = 20
    r.cost_estimate = 0.0
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.model = "qwen/qwen3-coder-30b"
    return r


def _run_capturing(tmp_path, *, turns: int, history_window_turns: int | None):
    """Drive the loop for `turns` turns; return (sent_sizes, last_messages).

    Each turn the model 'reads' a large file, so a full 3000-char tool result accumulates.
    `sent_sizes[i]` is len(json.dumps(req.messages)) — the ACTUAL payload sent on turn i.
    """
    big = tmp_path / "big.txt"
    big.write_text("X" * 8000)  # >3000 → _tool_read truncates to a full 3000-char dump

    sent_sizes: list[int] = []
    last_messages: dict = {}
    seq = {"n": 0}

    def dispatch(req):
        sent_sizes.append(len(json.dumps(req.messages)))
        last_messages["msgs"] = [dict(m) for m in req.messages]
        seq["n"] += 1
        return _read_response(str(big), seq["n"])

    device = MagicMock()
    device.dispatch.side_effect = dispatch

    kwargs = {"codec": NativeToolCodec(), "max_turns": turns, "inference_device": device}
    if history_window_turns is not None:
        kwargs["history_window_turns"] = history_window_turns
    loop = AgenticLoop(**kwargs)
    loop.run(system_prompt="sys", initial_message="do the thing", cwd=tmp_path)
    return sent_sizes, last_messages["msgs"]


# ── 1. THE PROOF NODE: history stays bounded over many turns ──────────────────


def test_history_stays_bounded_over_many_turns(tmp_path):
    """The re-sent history must stay under a fixed bound and plateau — NOT grow with turns.

    Proof node: at HEAD compaction is on by default, so 200 turns of 3000-char dumps stay
    small and flat. Reverting the compaction impl makes the history grow linearly (~200 ×
    3000+ chars), blowing the bound → authentic AssertionError.
    """
    BOUND = 60_000  # chars; a bounded window of ~10 dumps sits far below this
    sent_sizes, _ = _run_capturing(tmp_path, turns=200, history_window_turns=None)

    assert len(sent_sizes) == 200, "loop should have run the full 200 turns"
    assert max(sent_sizes) < BOUND, (
        f"re-sent history must stay under {BOUND} chars — peaked at {max(sent_sizes)} "
        f"(linear accumulation = the num_ctx-overflow bug)"
    )
    # Plateau, not linear: once the window fills, later turns are ~the same size as mid-run.
    # Linear growth would make turn 199 many times turn 30. Allow one group of slack.
    assert sent_sizes[-1] - sent_sizes[30] < 6_000, (
        f"history must plateau, not grow: turn30={sent_sizes[30]} turn199={sent_sizes[-1]}"
    )


# ── 2. Compaction keeps the message sequence API-valid ────────────────────────


def test_compaction_preserves_tool_call_pairing(tmp_path):
    """After many turns of eviction, the retained sequence must still be endpoint-valid.

    A MagicMock device ignores tool_call_id pairing, so the bound test alone would pass even
    on a sequence a real endpoint 400s on. This asserts the structural invariant directly:
    first message is the user task; every role:tool is immediately preceded by an assistant
    whose tool_calls carries the matching id; no assistant tool_call is left without a result.
    """
    _, messages = _run_capturing(tmp_path, turns=40, history_window_turns=10)

    # Compaction actually happened (40 turns, window 10 → far fewer than 40 groups retained).
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    assert assistant_count <= 10, f"window=10 should retain ≤10 groups, got {assistant_count}"

    # (a) First retained message is the task, not an orphaned tool result.
    assert messages[0]["role"] == "user", "retained history must start with the user task"

    # (b) Every tool result is preceded by an assistant advertising its call id, and every
    #     advertised call id is answered by a following tool result (no dangling call).
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            assert i > 0 and messages[i - 1].get("role") == "assistant", (
                f"tool result at {i} not preceded by an assistant (orphan → 400)"
            )
            advertised = {c["id"] for c in messages[i - 1].get("tool_calls", [])}
            assert m["tool_call_id"] in advertised, (
                f"tool result {m['tool_call_id']} has no matching tool_call in the prior assistant"
            )

    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            answered = {
                nxt["tool_call_id"]
                for nxt in messages[i + 1:]
                if nxt.get("role") == "tool"
            }
            for call in m["tool_calls"]:
                assert call["id"] in answered, (
                    f"assistant tool_call {call['id']} at {i} has no following tool result "
                    f"(dangling call → 400)"
                )


# ── 3. The window is configurable / can be disabled ───────────────────────────


def test_history_window_is_configurable_and_disable(tmp_path):
    """A smaller window keeps less; window=0 disables compaction (history grows unbounded)."""
    small, _ = _run_capturing(tmp_path, turns=40, history_window_turns=3)
    wide, _ = _run_capturing(tmp_path, turns=40, history_window_turns=10)
    off, _ = _run_capturing(tmp_path, turns=40, history_window_turns=0)

    assert max(small) < max(wide) < max(off), (
        f"tighter window → smaller payload; off → unbounded. "
        f"small={max(small)} wide={max(wide)} off={max(off)}"
    )
    # window=0 must reproduce the linear-growth bug (last turn far bigger than an early turn).
    assert off[-1] > off[5] * 3, "window=0 must leave history growing linearly"
