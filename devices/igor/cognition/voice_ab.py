"""
voice_ab.py — T-voice-actor-ab-framework (#439)

Substrate graduation for voice production. Two actors render the same
DecisionBlob into text; the framework compares, picks a winner, and
trains the substrate from the LLM when the LLM wins.

## Architectural role (Akien 2026-04-19)

Final stage of Igor's output-side pipeline. By the time a blob lands
here, the input-side trees have already decided WHAT Igor wants to say
(DecisionBlob.selected_action). This module decides HOW he says it —
in whose voice. Two voice actors compete:

  - Trees (GraphVoiceActor) — Igor's own voice, grown from his
    generation word graph.
  - LLM (LLMVoiceActor) — a separate voice actor; sounds like the
    model, not necessarily like Igor.

The A/B comparison lets Igor's own voice win when it's good enough,
which is the graduation mechanism: the LLM voice actor retires when
the graph voice consistently scores higher. That's the design-intent
answer to "Igor should select his own output when it's better."

## Actors

  GraphVoiceActor — uses Igor's generation word graph (G37) to render
    a decision blob's key tokens into text. Currently primitive (token
    extension from blob fields). Gets better as the graph trains.

  LLMVoiceActor — calls gateway.reason with voice_context() to produce
    character-coherent text. This is the current production path.

## Graduation

  LLM output trains the generation graph via reinforce_text().
  When the graph's output is consistently picked over the LLM's,
  the LLM actor stops being called — graduated.

## CP grounding

  CP1 — both outputs logged with provenance; no hidden winner selection
  CP3 — comparison scores + winner recorded per-turn in JSONL log
  CP6 — the framework never commits; it produces candidates that the
    existing VoiceProducer selects from
"""

from __future__ import annotations
from ..igor_base import IgorBase

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .decision_blob import DecisionBlob
from .prompt_contexts import PromptContext
from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex
    from .inference_gateway import InferenceGateway
    from .word_graph import WordGraph

logger = get_logger(__name__)


@dataclass
class VoiceCandidate:
    """One actor's rendering of a decision blob."""

    source: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceComparison:
    """The result of comparing two voice candidates."""

    graph_candidate: VoiceCandidate
    llm_candidate: VoiceCandidate
    winner: str
    winner_text: str
    graph_score: float = 0.0
    llm_score: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class GraphVoiceActor(IgorBase):
    """Renders a DecisionBlob using the generation word graph.

    Strategy: extract key tokens from the blob, use predict_next to
    extend them, assemble into a sentence. Primitive now, but improves
    as the graph trains from LLM outputs.
    """

    def __init__(self, word_graph: Optional["WordGraph"] = None) -> None:
        self._wg = word_graph

    def render(self, blob: DecisionBlob, ctx: PromptContext) -> VoiceCandidate:
        if self._wg is None:
            return VoiceCandidate(
                source="graph",
                text="",
                score=0.0,
                metadata={"reason": "no word graph available"},
            )

        seed_text = self._extract_seed(blob)
        if not seed_text:
            return VoiceCandidate(
                source="graph",
                text="",
                score=0.0,
                metadata={"reason": "no seed text from blob"},
            )

        predictions = self._wg.predict_next(seed_text, n=10)
        if not predictions:
            return VoiceCandidate(
                source="graph",
                text=seed_text,
                score=0.1,
                metadata={"reason": "no graph predictions", "seed": seed_text},
            )

        extended = self._assemble(seed_text, predictions)
        flatness = 1.0
        if predictions:
            max_w = predictions[0][1]
            flatness = max(0.0, 1.0 - min(max_w / 1.0, 1.0))

        return VoiceCandidate(
            source="graph",
            text=extended,
            score=1.0 - flatness,
            metadata={
                "seed": seed_text[:100],
                "prediction_count": len(predictions),
                "flatness": round(flatness, 3),
            },
        )

    def _extract_seed(self, blob: DecisionBlob) -> str:
        """Pull the most informative text from the blob."""
        if blob.selected_action:
            return blob.selected_action[:200]
        if blob.hypothesis:
            return blob.hypothesis[:200]
        proposed = getattr(blob, "proposed_experiment", None)
        if proposed and hasattr(proposed, "probe"):
            return proposed.probe[:200]
        return ""

    def _assemble(self, seed: str, predictions: list[tuple[str, float]]) -> str:
        """Build a sentence from seed + top predictions."""
        words = seed.split()
        seen = set(w.lower() for w in words)
        for word, weight in predictions:
            if word.lower() not in seen and len(word) > 2:
                words.append(word)
                seen.add(word.lower())
                if len(words) >= 20:
                    break
        return " ".join(words)


class LLMVoiceActor(IgorBase):
    """Renders a DecisionBlob using the LLM with voice_context."""

    def __init__(
        self,
        cortex: "Cortex",
        gateway: Optional["InferenceGateway"] = None,
    ) -> None:
        self.cortex = cortex
        self._gateway = gateway

    @property
    def gateway(self) -> "InferenceGateway":
        if self._gateway is None:
            from .inference_gateway import get_gateway

            self._gateway = get_gateway()
        return self._gateway

    def render(self, blob: DecisionBlob, ctx: PromptContext) -> VoiceCandidate:
        prompt = self._build_prompt(blob, ctx)
        try:
            text, cost, used_api = self.gateway.reason(
                prompt,
                relevant=[],
                core=[],
                level="interactive",
                cortex=self.cortex,
            )
        except Exception as exc:
            logger.warning("LLMVoiceActor gateway.reason failed: %s", exc)
            return VoiceCandidate(
                source="llm",
                text="",
                score=0.0,
                metadata={"error": str(exc)},
            )

        return VoiceCandidate(
            source="llm",
            text=text or "",
            score=0.8 if text else 0.0,
            metadata={"cost": cost, "used_api": used_api},
        )

    def _build_prompt(self, blob: DecisionBlob, ctx: PromptContext) -> str:
        parts = [ctx.system_text[:2000]] if ctx.system_text else []
        parts.append(
            f"Render this decision as Igor's reply to the user.\n"
            f"Intent: {blob.intent.value}\n"
        )
        if blob.selected_action:
            parts.append(f"Action: {blob.selected_action}")
        if blob.hypothesis:
            parts.append(f"Hypothesis: {blob.hypothesis}")
        proposed = getattr(blob, "proposed_experiment", None)
        if proposed:
            parts.append(f"Probe: {getattr(proposed, 'probe', '')}")
        parts.append(
            "\nRespond in Igor's voice. Be direct, confident in process. "
            "No hedging about knowledge — state the current best guess as fact."
        )
        return "\n\n".join(parts)


def compare_candidates(
    graph: VoiceCandidate,
    llm: VoiceCandidate,
) -> VoiceComparison:
    """Score and pick a winner between graph and LLM candidates.

    Scoring is deliberately simple for MVP:
    - Empty text = score 0
    - LLM starts with a baseline advantage (0.8 vs graph's dynamic score)
    - Graph wins only when its score exceeds LLM's by the graduation margin

    As the graph trains, its scores will rise. When it consistently
    beats the LLM, graduation has occurred.
    """
    g_score = graph.score if graph.text else 0.0
    l_score = llm.score if llm.text else 0.0

    if g_score > l_score and graph.text:
        winner = "graph"
        winner_text = graph.text
    elif llm.text:
        winner = "llm"
        winner_text = llm.text
    elif graph.text:
        winner = "graph"
        winner_text = graph.text
    else:
        winner = "none"
        winner_text = ""

    return VoiceComparison(
        graph_candidate=graph,
        llm_candidate=llm,
        winner=winner,
        winner_text=winner_text,
        graph_score=g_score,
        llm_score=l_score,
    )


class VoiceABFramework(IgorBase):
    """Orchestrates the A/B comparison and training loop.

    Per turn:
    1. Both actors render the same blob
    2. compare_candidates picks the winner
    3. Winner's text becomes the reply
    4. If LLM won, reinforce_text trains the generation graph
    5. Log the comparison to JSONL
    """

    def __init__(
        self,
        graph_actor: GraphVoiceActor,
        llm_actor: LLMVoiceActor,
        word_graph: Optional["WordGraph"] = None,
        log_dir: Optional[Path] = None,
    ) -> None:
        self.graph_actor = graph_actor
        self.llm_actor = llm_actor
        self._wg = word_graph
        self._log_dir = log_dir
        self._comparisons: list[VoiceComparison] = []

    def produce(self, blob: DecisionBlob, ctx: PromptContext) -> str:
        """Run both actors, compare, train, return winner's text."""
        graph_candidate = self.graph_actor.render(blob, ctx)
        llm_candidate = self.llm_actor.render(blob, ctx)

        comparison = compare_candidates(graph_candidate, llm_candidate)
        self._comparisons.append(comparison)

        if comparison.winner == "llm" and self._wg is not None and llm_candidate.text:
            try:
                self._wg.reinforce_text(llm_candidate.text, boost=0.03)
            except Exception as exc:
                logger.debug("voice_ab reinforce_text failed: %s", exc)

        self._log_comparison(comparison)
        return comparison.winner_text

    def stats(self) -> dict[str, Any]:
        """Summary statistics for the A/B comparison history."""
        total = len(self._comparisons)
        if total == 0:
            return {"total": 0, "graph_wins": 0, "llm_wins": 0, "graduation_pct": 0.0}
        graph_wins = sum(1 for c in self._comparisons if c.winner == "graph")
        return {
            "total": total,
            "graph_wins": graph_wins,
            "llm_wins": total - graph_wins,
            "graduation_pct": round(graph_wins / total * 100, 1),
        }

    def _log_comparison(self, comparison: VoiceComparison) -> None:
        """Append comparison to JSONL log."""
        try:
            log_dir = self._log_dir
            if log_dir is None:
                try:
                    from ..paths import paths as _paths

                    log_dir = _paths().logs / "voice_ab"
                except Exception:
                    log_dir = Path("/tmp/igor_voice_ab")
            log_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            log_file = log_dir / f"{today}_voice_ab.jsonl"
            entry = {
                "ts": comparison.timestamp,
                "winner": comparison.winner,
                "graph_score": comparison.graph_score,
                "llm_score": comparison.llm_score,
                "graph_text_len": len(comparison.graph_candidate.text),
                "llm_text_len": len(comparison.llm_candidate.text),
                "graph_meta": comparison.graph_candidate.metadata,
            }
            with open(log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.debug("voice_ab log failed: %s", exc)
