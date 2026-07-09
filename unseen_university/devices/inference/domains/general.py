"""
general.py — GeneralDomain: the default domain, and the prototype chatbot.

T-general-domain-chat-prototype. "I just realized… the default is the 'general' domain. So
there is always a domain, and everybody else descends from that." (Akien, 2026-07-08.)

One attempt here is ONE dispatch — no agentic loop, no tools, no repo state. That is the
point: chat is the simplest possible consumer of the escalation walk. If escalation cannot
be made correct on one dispatch and one reply, it cannot be made correct anywhere. The
coding ladder is the hard case and waits behind this (T-inference-tier-ladder-real).

What this domain owns
---------------------
- The DEFAULT escalation policy (D-domains-general-with-device-owned-specializations,
  Amendment 1: the default policy lives on the general domain, NOT on the proxy). The walk
  itself is inherited from BaseDomain and untouched — it stays the single money-safety owner:
  a CAPABILITY failure bumps one rung and spends up; an AVAILABILITY failure re-selects at
  the same rung and does not.
- The COMPLETION CONTRACT: what counts as an answer. See below — it is the whole game.

The completion contract, and the trap it exists to avoid
--------------------------------------------------------
`answer_check` decides DONE vs escalate. It MUST NOT be the model's self-report. A weak model
asked a hard question does not say "I don't know" — it returns a confident wrong answer. On
2026-07-08 `deepseek-r1:14b` returned `{"status":"done","result":"wrote smoke file"}` and
wrote nothing; every check that believed it went green. An escalation trigger that reads a
self-reported confidence or refusal token therefore never fires on precisely the queries it
exists for.

For the eval, `answer_check` is ground truth (escalation_corpus.EvalQuery.verify). For open
chat with no ground truth, the runtime trigger is an OPEN QUESTION — a judge model? a
self-consistency vote? — and this module does not pretend to have solved it. The default
check accepts any non-empty reply and is named `_any_nonempty_reply_is_done` so nobody can
mistake it for a capability signal. That is the deferred lever, stated out loud (CP1).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from unseen_university.devices.inference.domains.agentic_loop import (
    LOOP_AVAILABILITY,
    LOOP_DONE,
    LOOP_ESCALATE,
    LoopResult,
)
from unseen_university.devices.inference.domains.base import BaseDomain

log = logging.getLogger(__name__)


def _any_nonempty_reply_is_done(reply: str) -> bool:
    """The DEFAULT completion contract — and a NAMED GAP, not a capability signal.

    It cannot tell a correct answer from a confident wrong one, so under this check the
    escalation walk fires on an empty/blank reply and on nothing else. Injecting a real
    check (ground truth in the eval; a judge or self-consistency vote at runtime) is the
    deferred lever. Do not "improve" this into something that merely looks smarter — a
    self-report ('I'm not sure') is worse than useless, because a confabulating model does
    not emit one.
    """
    return bool((reply or "").strip())


class GeneralDomain(BaseDomain):
    """The default domain: a chat turn, escalated by an answer check.

    Every other domain descends from this one. `name` is carried through verbatim (an
    unregistered domain is a GeneralDomain wearing that name), so the resolver still sees the
    exact domain dimension the caller asked for.
    """

    name: str = ""
    task_class: str = "worker"
    critic_enabled: bool = False
    aci_mode: bool = False

    #: A chat turn is one call. A blank/failed reply is worth one cheap retry at the same
    #: rung (a source blip), but a persistent outage must not loop onto paid tiers.
    max_availability_retries: int = 2

    def __init__(
        self,
        name: str | None = None,
        *,
        harvest_mode: bool = False,
        answer_check: Callable[[str], bool] | None = None,
        inference_device=None,
        # A reasoning model spends its budget inside <think> before writing a word of answer.
        # 1024 truncates deepseek-r1 mid-thought; the check then fails and the walk classifies
        # it as CAPABILITY and spends up a tier for want of tokens
        # (T-escalation-truncation-misclassified-as-capability).
        max_tokens: int = 4096,
        # The escalation rungs are on-box and cost $0/second, and a 32b reasoning model takes
        # 110-190s on Hex. At 120s every attempt at the stronger rung timed out, read as
        # AVAILABILITY ("source down"), retried in place, and exhausted — a slow-but-healthy
        # local model is not a down source. AgenticLoop already raises its wall for local /
        # flat-rate sources on turn 0; a single dispatch has no turn 0 to learn from, so the
        # default starts wide. Cheap to wait, expensive to misread.
        timeout: int = 300,
    ) -> None:
        super().__init__(name, harvest_mode=harvest_mode)
        self._answer_check = answer_check or _any_nonempty_reply_is_done
        self._inference_device = inference_device
        self._max_tokens = max_tokens
        self._timeout = timeout
        if answer_check is None:
            log.info(
                "GeneralDomain(name=%r): no answer_check — the default contract accepts any "
                "non-empty reply and CANNOT detect a confabulation, so escalation will not "
                "fire on a confident wrong answer",
                self.name,
            )

    # ── the chat entry point ─────────────────────────────────────────────────

    def ask(self, query: str, *, query_id: str = "chat", agent_id: str = "") -> str | None:
        """Answer `query`, escalating on a failed answer check. None = the walk halted.

        Delegates to the inherited escalation walk (`BaseDomain.run`) rather than
        reimplementing it — the walk is the single money-safety owner, and a second copy of
        the capability-vs-availability split is a second place for it to be wrong. The walk
        speaks in work-items, so a chat query is wrapped as one.
        """
        return self.run({"id": query_id, "title": query, "description": query},
                        agent_id=agent_id)

    # ── seams the walk drives ────────────────────────────────────────────────

    def _initial_message(self, ticket: dict) -> str:
        """A chat query is the message. No ticket formatting, no tags, no description block."""
        return ticket.get("description") or ticket.get("title", "")

    def _run_attempt(
        self,
        *,
        system_prompt: str,
        ticket: dict,
        ticket_id: str,
        agent_id: str,
        escalation_hop: int,
        prior_attempt: str,
        cwd: Path | None = None,
    ) -> LoopResult:
        """ONE dispatch. The reply is DONE iff it passes this domain's answer check.

        `cwd` is accepted and ignored: a chat turn touches no working directory. Keeping the
        signature identical to the base means the walk drives every domain through one seam.
        """
        # Function-local by DEFERRAL, not to hide a cycle. domain -> proxy is the correct
        # direction (the proxy imports nothing above it — test_proxy_domain_layering pins
        # that). Importing device at module scope would make `import ...domains` eagerly pull
        # in a psycopg2-bound device, which the hermetic tests must not need. Same reason
        # AgenticLoop defers it.
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        device = self._inference_device or InferenceDevice()
        request = InferenceRequest(
            messages=[{"role": "user", "content": self._initial_message(ticket)}],
            system=system_prompt,
            task_class=self.task_class,
            domain=self.name,
            ticket_id=ticket_id,
            agent_id=agent_id,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            temperature=0.0,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
        )

        log.info("domain=%s crossing|step=dispatch|ticket=%s|hop=%d|tools=none",
                 self.name or "(general)", ticket_id, escalation_hop)
        try:
            response = device.dispatch(request)
        except Exception as exc:
            # A raise means a source went down mid-call. AVAILABILITY, never capability —
            # capability is the only class that spends up a tier.
            log.error("domain=%s: dispatch raised for %s (hop=%d): %s",
                      self.name, ticket_id, escalation_hop, exc)
            return LoopResult(LOOP_AVAILABILITY, text=str(exc), turns=0)

        if response.finish_reason == "error" or response.source_kind == "none":
            log.warning("domain=%s: no live source (finish=%s kind=%s) for %s — availability",
                        self.name, response.finish_reason, response.source_kind, ticket_id)
            return LoopResult(LOOP_AVAILABILITY, text=response.text or "", turns=0)

        text = response.text or ""
        passed = self._answer_check(text)
        log.info("domain=%s crossing|step=check|ticket=%s|hop=%d|passed=%s|model=%s",
                 self.name or "(general)", ticket_id, escalation_hop, passed, response.model)
        return LoopResult(
            LOOP_DONE if passed else LOOP_ESCALATE,
            text=text,
            turns=1,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_estimate,
        )
