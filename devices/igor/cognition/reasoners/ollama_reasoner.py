"""
Ollama local reasoner.
Runs on-device. No API cost. Used for fast pre-parsing and habit matching,
not for heavy reasoning (that stays with Anthropic).
"""

import json
import ollama as _ollama
from ...memory.models import Memory
from .base import BaseReasoner

DEFAULT_MODEL = "llama3.2:1b"


class OllamaReasoner(BaseReasoner):
    """Full reasoning via local Ollama model. Slow but free."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model

    def name(self) -> str:
        return f"Ollama/{self.model}"

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
    ) -> tuple[str, float]:
        memory_context = ""
        if relevant_memories:
            memory_context = "\n\nRelevant memories:\n" + "\n".join(
                f"- {m.narrative}" for m in relevant_memories[:5]
            )

        response = _ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": user_input + memory_context}],
        )
        return response["message"]["content"], 0.0  # Local = no cost


def preparse(user_input: str, habits: list[Memory], model: str = DEFAULT_MODEL) -> dict:
    """
    Use local 1B model to cheaply preprocess input before touching the API.

    Returns a dict with:
      - intent: classified intent string
      - keywords: list of key terms
      - habit_match: Memory or None if a habit likely applies
      - confidence: 0.0-1.0 how confident we are a habit covers this
      - should_escalate: bool - True means send to Anthropic API
    """
    # Build habit descriptions for the prompt
    habit_desc = ""
    if habits:
        habit_desc = "\n\nAvailable habits:\n" + "\n".join(
            f"- ID={h.id}: trigger='{h.metadata.get('trigger', '')}' desc='{h.narrative[:60]}'"
            for h in habits
        )

    prompt = f"""Classify this user input. Reply with ONLY a JSON object, no other text.

User input: "{user_input}"{habit_desc}

JSON fields:
- intent: one word from this list only: greeting, meta_question, factual_question, action_request, memory_instruction, general
- keywords: array of 2-4 important words from the input
- habit_id: the habit ID string if a habit matches, or null
- confidence: number from 0.0 to 1.0 for how well a habit matches
- should_escalate: true if needs deep reasoning, false if simple

Example output:
{{"intent": "factual_question", "keywords": ["capital", "france"], "habit_id": null, "confidence": 0.0, "should_escalate": true}}"""

    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},  # Low temp for consistent classification
        )
        text = response["message"]["content"].strip()

        # Extract JSON even if model adds surrounding text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
        else:
            raise ValueError("No JSON found in response")

        # Resolve habit_id to actual Memory object
        habit_match = None
        if parsed.get("habit_id") and habits:
            habit_match = next((h for h in habits if h.id == parsed["habit_id"]), None)

        return {
            "intent": parsed.get("intent", "general"),
            "keywords": parsed.get("keywords", []),
            "habit_match": habit_match,
            "confidence": float(parsed.get("confidence", 0.0)),
            "should_escalate": bool(parsed.get("should_escalate", True)),
        }

    except Exception:
        # If local model fails, escalate to API - never block on local failure
        return {
            "intent": "general",
            "keywords": [],
            "habit_match": None,
            "confidence": 0.0,
            "should_escalate": True,
        }


def score_memories(query: str, memories: list[Memory], model: str = DEFAULT_MODEL, top_n: int = 5) -> list[Memory]:
    """
    Use local model to score memory relevance rather than naive text search.
    Returns top_n most relevant memories.
    """
    if not memories:
        return []

    # Build compact memory list for scoring
    mem_list = "\n".join(
        f"{i}: [{m.memory_type.value}] {m.narrative[:80]}"
        for i, m in enumerate(memories[:20])  # Cap at 20 to keep prompt short
    )

    prompt = f"""Given this query: "{query}"

Rate each memory's relevance (0-10). Reply with ONLY a JSON array of [index, score] pairs, most relevant first. Example: [[2,9],[0,7],[1,3]]

Memories:
{mem_list}"""

    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        text = response["message"]["content"].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0:
            return memories[:top_n]

        scores = json.loads(text[start:end])
        ranked = sorted(scores, key=lambda x: x[1], reverse=True)
        result = []
        for idx, score in ranked[:top_n]:
            if 0 <= idx < len(memories) and score > 0:
                result.append(memories[idx])
        return result if result else memories[:top_n]

    except Exception:
        return memories[:top_n]
