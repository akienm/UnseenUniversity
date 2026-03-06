"""
Thalamus - input processing and routing.
Parses intent, emotional tone, and determines what memories to activate.
"""

import re
from dataclasses import dataclass
from .local_pool import LocalKoboldPool


class Thalamus:
    def __init__(self):
        self.pool = LocalKoboldPool()
        self.preparse_host = self.pool.select_preparse_host()

    def process(self, raw_input: str) -> "ParsedInput":
        text = raw_input.strip()

        # Command detection
        is_command = text.startswith("/")
        command = text[1:].split()[0].lower() if is_command else None

        # Keyword extraction (simple - improve with NLP later)
        keywords = _extract_keywords(text)

        # Intent classification (simple rules - improve with LLM later)
        intent = _classify_intent(text, keywords)

        # Tone detection
        tone = _detect_tone(text)

        routing_directive = _detect_routing_directive(text)

        return ParsedInput(
            raw=text,
            intent=intent,
            keywords=keywords,
            tone=tone,
            is_command=is_command,
            command=command,
            routing_directive=routing_directive,
        )


@dataclass
class ParsedInput:
    raw: str
    intent: str
    keywords: list
    tone: str          # friendly, neutral, frustrated, curious, urgent
    is_command: bool   # starts with / or is a system command
    command: str | None = None
    routing_directive: str = ""  # "local_only" | "" — from user instruction (#90)


_LOCAL_ONLY_PHRASES = (
    "local only", "local-only", "using only local", "no cloud",
    "local resources only", "stay local", "offline mode",
)


def _detect_routing_directive(text: str) -> str:
    """Detect explicit routing constraints in natural language (#90)."""
    t = text.lower()
    if any(p in t for p in _LOCAL_ONLY_PHRASES):
        return "local_only"
    return ""


def _extract_keywords(text: str) -> list:
    # Remove common stop words, extract meaningful terms
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "i", "you", "he", "she", "it", "we", "they", "what", "which",
        "who", "how", "when", "where", "why", "that", "this", "these",
        "those", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "about", "as", "into", "through",
        # NOTE: 'igor' and 'akien' intentionally excluded (#85) — highest-signal terms
    }
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    return [w for w in words if w not in stop_words]


def _classify_intent(text: str, keywords: list) -> str:
    text_lower = text.lower()

    if any(w in text_lower for w in ["hello", "hi ", "hey", "good morning", "good evening"]):
        return "greeting"
    if any(w in text_lower for w in ["how do you work", "how are you", "what are you", "who are you"]):
        return "meta_question"
    if any(w in text_lower for w in ["why did you", "why are you", "explain", "reasoning"]):
        return "explanation_request"
    if any(w in text_lower for w in ["capital of", "what is", "what's", "tell me about"]):
        return "factual_question"
    if any(w in text_lower for w in ["remember", "save", "note that", "learn that"]):
        return "memory_instruction"
    if any(w in text_lower for w in ["do ", "run ", "execute", "search", "find", "browse"]):
        return "action_request"
    if text_lower.startswith("/"):
        return "command"
    return "general"


def _detect_tone(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["!", "urgent", "asap", "immediately", "now"]):
        return "urgent"
    if any(w in text_lower for w in ["?", "how", "why", "what", "curious"]):
        return "curious"
    if any(w in text_lower for w in ["frustrated", "annoyed", "wrong", "broken", "stupid"]):
        return "frustrated"
    if any(w in text_lower for w in ["hello", "hi", "hey", "thanks", "please", "great"]):
        return "friendly"
    return "neutral"
