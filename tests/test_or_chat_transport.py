"""
test_or_chat_transport.py — T-uc-chattable-llm-channel MVP tests.

Verifies:
  1. System prompt + user turn assembly renders correctly.
  2. Scrollback trimming drops oldest turns when over budget; system
     prompt is never trimmed.
  3. A chat/user send triggers a gateway call and emits chat/assistant.
  4. purpose extraction from comms://or-chat/<purpose>/<session> URIs.

Gateway is stubbed — no real LLM calls.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.utility_closet.comms import Channel, ChannelMessage, Direction
from lab.utility_closet.transports.or_chat import (
    CT_ASSISTANT,
    CT_SYSTEM,
    CT_USER,
    OR_CHAT_URI_PREFIX,
    OrChatTransport,
)


class _StubStore:
    """Replaces PostgresTransport for test isolation."""

    def __init__(self):
        self.messages: list[ChannelMessage] = []

    def send(self, channel, message):
        self.messages.append(message)
        return True

    def read(self, channel, limit=50, since=None):
        return list(reversed(self.messages[-limit:]))

    def close(self):
        pass


def _mk_channel(addr: str = "comms://or-chat/qwen/session-a") -> Channel:
    return Channel(address=addr, direction=Direction.READ_WRITE)


def _mk_transport(
    budget: int = 16000,
    limit: int = 50,
) -> OrChatTransport:
    t = OrChatTransport(
        db_url="postgres://unused",
        scrollback_budget_chars=budget,
        scrollback_limit=limit,
    )
    t._inner._store = _StubStore()
    return t


class TestPromptAssembly(unittest.TestCase):
    def test_renders_system_and_turns(self):
        t = _mk_transport()
        sys_msg = ChannelMessage(content_type=CT_SYSTEM, payload="You are helpful.")
        u1 = ChannelMessage(content_type=CT_USER, payload="Hi.")
        a1 = ChannelMessage(content_type=CT_ASSISTANT, payload="Hello!")
        u2 = ChannelMessage(content_type=CT_USER, payload="How are you?")
        prompt = t._render_prompt(sys_msg, [u1, a1, u2])

        self.assertIn("System: You are helpful.", prompt)
        self.assertIn("User: Hi.", prompt)
        self.assertIn("Assistant: Hello!", prompt)
        self.assertIn("User: How are you?", prompt)
        self.assertTrue(prompt.endswith("Assistant:"))

    def test_renders_without_system_prompt(self):
        t = _mk_transport()
        u1 = ChannelMessage(content_type=CT_USER, payload="Hey")
        prompt = t._render_prompt(None, [u1])
        self.assertFalse(prompt.startswith("System:"))
        self.assertIn("User: Hey", prompt)


class TestTrimming(unittest.TestCase):
    def test_drops_oldest_when_over_budget(self):
        t = _mk_transport(budget=40)
        turns = [
            ChannelMessage(content_type=CT_USER, payload="a" * 20),
            ChannelMessage(content_type=CT_ASSISTANT, payload="b" * 20),
            ChannelMessage(content_type=CT_USER, payload="c" * 20),
        ]
        trimmed = t._trim_to_budget(turns)
        # Budget 40; 20+20+20=60 over. Drop oldest (20) → 40, fits. Keep 2.
        self.assertEqual(len(trimmed), 2)
        self.assertEqual(trimmed[0].payload, "b" * 20)
        self.assertEqual(trimmed[1].payload, "c" * 20)

    def test_respects_reserve(self):
        t = _mk_transport(budget=60)
        turns = [
            ChannelMessage(content_type=CT_USER, payload="x" * 20),
            ChannelMessage(content_type=CT_ASSISTANT, payload="y" * 20),
        ]
        # reserve 50 → effective budget 10 → drop both turns
        trimmed = t._trim_to_budget(turns, reserve_chars=50)
        self.assertEqual(trimmed, [])

    def test_keeps_all_when_under_budget(self):
        t = _mk_transport(budget=1000)
        turns = [ChannelMessage(content_type=CT_USER, payload="short")]
        trimmed = t._trim_to_budget(turns)
        self.assertEqual(len(trimmed), 1)


class TestChatFlow(unittest.TestCase):
    def test_user_message_triggers_gateway_and_emits_assistant(self):
        t = _mk_transport()
        ch = _mk_channel()

        # Seed store with a system prompt + prior turn
        t._inner._store.send(
            ch,
            ChannelMessage(
                channel=ch.address,
                source="system",
                content_type=CT_SYSTEM,
                payload="You are a terse assistant.",
            ),
        )
        t._inner._store.send(
            ch,
            ChannelMessage(
                channel=ch.address,
                source="akien",
                content_type=CT_USER,
                payload="first turn",
            ),
        )
        t._inner._store.send(
            ch,
            ChannelMessage(
                channel=ch.address,
                source="inference-gateway",
                content_type=CT_ASSISTANT,
                payload="first reply",
            ),
        )

        # New user turn — this is what we're testing
        new_user = ChannelMessage(
            id="u-42",
            channel=ch.address,
            source="akien",
            content_type=CT_USER,
            payload="second turn",
        )
        with patch.object(
            t._inner, "_run_inference", return_value="second reply"
        ) as stub:
            self.assertTrue(t.send(ch, new_user))
            stub.assert_called_once()
            call_kwargs = stub.call_args.kwargs
            prompt = call_kwargs["prompt"]
            self.assertIn("You are a terse assistant.", prompt)
            self.assertIn("User: first turn", prompt)
            self.assertIn("Assistant: first reply", prompt)
            self.assertIn("User: second turn", prompt)
            self.assertEqual(call_kwargs["purpose"], "qwen")

        # Store has: system, user1, asst1, user2 (new), assistant2 (emitted)
        self.assertEqual(len(t._inner._store.messages), 5)
        last = t._inner._store.messages[-1]
        self.assertEqual(last.content_type, CT_ASSISTANT)
        self.assertEqual(last.payload, "second reply")
        self.assertEqual(last.reply_to, "u-42")

    def test_system_prompt_passes_through_no_gateway(self):
        t = _mk_transport()
        ch = _mk_channel()
        with patch.object(t._inner, "_run_inference") as stub:
            ok = t.send(
                ch,
                ChannelMessage(
                    channel=ch.address,
                    source="system",
                    content_type=CT_SYSTEM,
                    payload="Be concise.",
                ),
            )
            stub.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(len(t._inner._store.messages), 1)


class TestPurposeExtraction(unittest.TestCase):
    def test_or_chat_uri(self):
        t = _mk_transport()
        self.assertEqual(
            t._purpose_from_channel("comms://or-chat/gemini-pro/sess-1"),
            "gemini-pro",
        )

    def test_or_chat_uri_no_session(self):
        t = _mk_transport()
        self.assertEqual(
            t._purpose_from_channel("comms://or-chat/qwen"),
            "qwen",
        )

    def test_model_uri_backcompat(self):
        t = _mk_transport()
        self.assertEqual(
            t._purpose_from_channel("comms://model/tier2"),
            "tier2",
        )

    def test_unknown_uri_defaults(self):
        t = _mk_transport()
        self.assertEqual(
            t._purpose_from_channel("comms://shared"),
            "default",
        )


if __name__ == "__main__":
    unittest.main()
