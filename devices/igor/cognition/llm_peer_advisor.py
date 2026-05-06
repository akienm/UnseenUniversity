"""
llm_peer_advisor.py — T-llm-collaboration-protocol (#438)

The real PeerAdvisor that calls the inference gateway for each turn of
a reasoning workflow conversation. Replaces the ScriptedPeer test
double with a live LLM call using reasoning_context (small cockpit).

## Conversation protocol

Igor speaks first (opening utterance from the workflow). Then:

1. Build a prompt from reasoning_context + conversation history
2. Call gateway.reason() with the assembled prompt
3. Log the exchange to a JSONL transcript file
4. Return the LLM's response text to the workflow runner

The workflow runner decides whether to continue or exit based on the
peer's response (via workflow.next_utterance). The peer advisor has
no opinion about stopping — it just translates between the workflow
and the inference gateway.

## Logging console visibility (Akien requirement)

From Akien 2026-04-14: 'maybe we send those to the igor logging
console so i can see them.' Every reasoning conversation gets a JSONL
transcript at ~/.TheIgors/local/logs/reasoning_conversations/ with
one line per utterance. Akien can tail -f or grep these to spot-check
what Igor couldn't solve alone — the conversations expose the training
signal.

## CP grounding

- CP1 — the LLM prompt includes Igor's honest admission of what he
  doesn't know (the escalation trail from the cascade walker)
- CP3 — every turn is logged with provenance (who spoke, when, the
  reasoning_context that was active)
- CP6 — the prompt tags LLM output as a hypothesis; the reasoning
  workflow's WorkflowRecorder captures the match/mismatch signal for
  graduation training
"""

from __future__ import annotations
from ..igor_base import IgorBase

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.console import Console as _Console

from .prompt_contexts import Provenance as PCProvenance, reasoning_context
from .reasoning_workflow import Conversation, PeerAdvisor, Speaker
from ..igor_base import get_logger

_console = _Console(force_terminal=True)

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from .inference_gateway import InferenceGateway

logger = get_logger(__name__)


class LLMPeerAdvisor(PeerAdvisor, IgorBase):
    """Real peer advisor backed by the inference gateway. Each call to
    respond() fires one LLM turn using the reasoning_context prompt.
    """

    def __init__(
        self,
        cortex: "Cortex",
        gateway: Optional["InferenceGateway"] = None,
        *,
        milieu: Optional[dict[str, Any]] = None,
        identity: Optional[dict[str, Any]] = None,
        escalation_trail: Optional[list[dict[str, Any]]] = None,
        capabilities: Optional[list[str]] = None,
        log_dir: Optional[Path] = None,
        level: str = "interactive",
        on_tier: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cortex = cortex
        self._gateway = gateway
        self._milieu = milieu
        self._identity = identity
        self._escalation_trail = escalation_trail
        self._capabilities = capabilities
        self._level = level
        self._log_dir = log_dir
        self._log_file: Optional[Path] = None
        self._on_tier = on_tier

    @property
    def gateway(self) -> "InferenceGateway":
        if self._gateway is None:
            from .inference_gateway import get_gateway

            self._gateway = get_gateway()
        return self._gateway

    def respond(self, conversation: Conversation) -> str:
        """Call the LLM with the reasoning context + conversation
        history. Returns the LLM's response text."""
        # Build the reasoning prompt
        last_igor = conversation.last_igor()
        query = last_igor.content if last_igor else "(no igor utterance)"

        situation = {
            "query": query[:500],
            "context": {
                "conversation_id": conversation.conversation_id,
                "turn_number": conversation.length(),
                "workflow": conversation.workflow_name,
            },
        }

        prov = PCProvenance(
            caller="llm_peer_advisor",
            situation_source=f"workflow:{conversation.workflow_name}",
        )
        try:
            from .experiment_scheduler import recent_completed

            _recent_exps = recent_completed(self.cortex, limit=5)
        except Exception:
            _recent_exps = None

        ctx = reasoning_context(
            situation=situation,
            provenance=prov,
            milieu=self._milieu,
            identity=self._identity,
            escalation_trail=self._escalation_trail,
            capabilities=self._capabilities,
            recent_experiments=_recent_exps,
        )

        # Build the user prompt from conversation history
        history_lines = []
        for u in conversation.utterances:
            label = "Igor" if u.speaker == Speaker.IGOR else "Peer"
            history_lines.append(f"[{label}]: {u.content}")
        user_prompt = "\n\n".join(history_lines)

        turn_n = conversation.length()
        _console.print(f"[dim][↑ peer:{turn_n}] {user_prompt[:120]!r}[/]")

        # Call the gateway
        # is_user_turn: interactive-level peers consult cloud (peer reasoning
        # is high-stakes; Ollama stalls make it unresponsive for humans).
        try:
            response_text, cost, used_api = self.gateway.reason(
                user_input=user_prompt,
                relevant=[],
                core=[],
                level=self._level,
                cortex=self.cortex,
                is_user_turn=(self._level == "interactive"),
                on_tier=self._on_tier,
            )
        except Exception as exc:
            logger.warning("LLMPeerAdvisor gateway.reason raised: %s", exc)
            response_text = (
                f"(LLM reasoning call failed: {type(exc).__name__}. "
                "Returning to Igor's substrate for the next move.)"
            )
            cost = 0.0
            used_api = False

        _console.print(f"[dim][↓ peer:{turn_n}] {response_text[:120]!r}[/]")

        # Log the exchange
        self._log_turn(conversation, user_prompt, response_text, cost, used_api)

        return response_text

    # ── Logging ──────────────────────────────────────────────────────────────

    def _ensure_log_file(self, conversation: Conversation) -> Path:
        """Create the JSONL transcript file for this conversation if not
        already open."""
        if self._log_file is not None:
            return self._log_file

        log_dir = self._log_dir
        if log_dir is None:
            try:
                from ..paths import paths as _paths

                log_dir = _paths().logs / "reasoning_conversations"
            except Exception:
                log_dir = Path("/tmp/igor_reasoning_conversations")
        log_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{conversation.conversation_id}.jsonl"
        self._log_file = log_dir / filename
        return self._log_file

    def _log_turn(
        self,
        conversation: Conversation,
        user_prompt: str,
        response_text: str,
        cost: float,
        used_api: bool,
    ) -> None:
        """Append one turn to the JSONL transcript."""
        try:
            log_path = self._ensure_log_file(conversation)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "conversation_id": conversation.conversation_id,
                "workflow": conversation.workflow_name,
                "turn": conversation.length(),
                "igor_prompt_chars": len(user_prompt),
                "peer_response_chars": len(response_text),
                "cost_usd": cost,
                "used_api": used_api,
                "peer_response_preview": response_text[:500],
            }
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.debug("LLMPeerAdvisor log failed: %s", exc)
