"""
general.py — GeneralDomain: the default domain, and the prototype chatbot.

STUB (proof-first, T-general-domain-chat-prototype). `ask()` performs exactly one dispatch
and hands back whatever the model said. There is no escalation: a confident wrong answer at
the cheapest rung is returned as the answer. That is the RED — it is also, precisely, the
shipped bug of 2026-07-08 in miniature, so the red proof and the bug have the same shape.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from unseen_university.devices.inference.domains.base import BaseDomain

log = logging.getLogger(__name__)


def _any_nonempty_reply_is_done(reply: str) -> bool:
    """The DEFAULT completion contract — a NAMED GAP, not a capability signal."""
    return bool((reply or "").strip())


class GeneralDomain(BaseDomain):
    """The default domain: a chat turn. STUB — does not yet escalate."""

    name: str = ""
    task_class: str = "worker"
    critic_enabled: bool = False
    aci_mode: bool = False

    def __init__(
        self,
        name: str | None = None,
        *,
        harvest_mode: bool = False,
        answer_check: Callable[[str], bool] | None = None,
        inference_device=None,
        max_tokens: int = 1024,
        timeout: int = 120,
    ) -> None:
        super().__init__(name, harvest_mode=harvest_mode)
        self._answer_check = answer_check or _any_nonempty_reply_is_done
        self._inference_device = inference_device
        self._max_tokens = max_tokens
        self._timeout = timeout

    def ask(self, query: str, *, query_id: str = "chat", agent_id: str = "") -> str | None:
        """STUB: one dispatch, no escalation walk, no answer check."""
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        device = self._inference_device or InferenceDevice()
        response = device.dispatch(InferenceRequest(
            messages=[{"role": "user", "content": query}],
            task_class=self.task_class,
            domain=self.name,
            ticket_id=query_id,
            agent_id=agent_id,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            escalation_hop=0,
        ))
        return response.text
