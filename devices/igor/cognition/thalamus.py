"""
Thalamus - input processing and routing.
Parses intent, emotional tone, and determines what memories to activate.
"""

import re
from dataclasses import dataclass, field

from ..igor_base import IgorBase


class Thalamus(IgorBase):
    def __init__(self):
        super().__init__()

    def process(self, raw_input: str) -> "ParsedInput":
        text = raw_input.strip()

        # Extract the actual user message, stripping prepended thread context.
        # Habit trigger scoring should fire on what the user *said*, not on
        # prior exchange history that happens to contain trigger words.
        # [Thread context — recent exchanges in this channel:] prefix ends at
        # the last bracketed message tag before the actual content.
        core_text = text
        _THREAD_MARKER = "[Thread context — recent exchanges in this channel:]"
        if _THREAD_MARKER in text:
            # Find the last "[Web message from", "[Discord message", "[Email from",
            # "CC:" etc. — the actual current message starts there.
            import re as _re

            _msg_start = _re.search(
                r"\[(?:Web message|Discord message|Email) from [^\]]+\]:|CC:", text
            )
            if _msg_start:
                core_text = text[_msg_start.start() :]

        # Strip [Routing directive: ...] suffix injected by CC bridge messages.
        # This prevents directive text ("background jobs", "inline") from
        # triggering threshold habits during habit scoring.
        _RD_MARKER = "[Routing directive:"
        if _RD_MARKER in core_text:
            core_text = core_text[: core_text.index(_RD_MARKER)].rstrip()

        # Command detection
        is_command = text.startswith("/")
        command = text[1:].split()[0].lower() if is_command else None

        # Keyword extraction — preserves technical tokens (#93 phase 1)
        keywords = _extract_keywords(core_text)

        # Intent classification — expanded taxonomy (#93 phase 1)
        intent = _classify_intent(core_text, keywords)

        # Complexity assessment — drives tier skip_to logic (#93 phase 1)
        complexity = _assess_complexity(core_text, keywords)

        # Tone detection
        tone = _detect_tone(core_text)

        routing_directive = _detect_routing_directive(core_text)
        output_complexity = _assess_output_complexity(core_text, intent)
        traversal_strategy, traversal_entry = _classify_question_traversal(
            core_text, intent
        )
        traversal_direction = _STRATEGY_DIRECTION.get(traversal_strategy, "")

        return ParsedInput(
            raw=text,
            core_input=core_text,
            intent=intent,
            keywords=keywords,
            tone=tone,
            is_command=is_command,
            command=command,
            routing_directive=routing_directive,
            complexity=complexity,
            output_complexity=output_complexity,
            traversal_strategy=traversal_strategy,
            traversal_entry=traversal_entry,
            traversal_direction=traversal_direction,
        )


@dataclass
class ParsedInput:
    raw: str
    core_input: str  # bare user message, thread-context prefix stripped — used for habit scoring
    intent: str
    keywords: list
    tone: str  # friendly, neutral, frustrated, curious, urgent
    is_command: bool  # starts with / or is a system command
    command: str | None = None
    routing_directive: str = ""  # "local_only" | "" — from user instruction (#90)
    complexity: str = "medium"  # "low" | "medium" | "high" — #93 tier hint
    output_complexity: str = "medium"  # "low" | "medium" | "high" — #154 tier.0 gate
    traversal_strategy: str = (
        ""  # #181: "semantic_depth"|"causal_trace"|"lever_trace"|"broad_search"|"factual_leaf"|"memory_verify"|"attractor_hold"|""
    )
    traversal_entry: str = (
        ""  # #181: "semantic_anchor"|"cp_closest"|"twm_attractor"|"ring_recent"|""
    )
    traversal_direction: str = (
        ""  # #182: "up"|"down"|"lateral"|"lookup"|"" — fundamental direction; strategies are shortcuts
    )


_LOCAL_ONLY_PHRASES = (
    "local only",
    "local-only",
    "using only local",
    "no cloud",
    "local resources only",
    "stay local",
    "offline mode",
)


def _detect_routing_directive(text: str) -> str:
    """Detect explicit routing constraints in natural language (#90)."""
    t = text.lower()
    if any(p in t for p in _LOCAL_ONLY_PHRASES):
        return "local_only"
    return ""


# #100: Proper nouns that must survive stop-word filtering regardless of casing.
PROPER_NOUN_WHITELIST: frozenset[str] = frozenset(
    {
        "igor",
        "akien",
        "leah",
        "claude",
        "confluence",
        "discord",
        "openrouter",
        "ollama",
        "anthropic",
    }
)


def _extract_keywords(text: str) -> list:
    """
    #93 phase 1: Expanded keyword extraction — preserves technical tokens.
    Adds: version numbers, file extensions, camelCase splits, quoted strings,
    numeric identifiers alongside normal alpha words.
    """
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "that",
        "this",
        "these",
        "those",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "about",
        "as",
        "into",
        "through",
    }

    keywords = []

    # Quoted strings — extract verbatim (high signal)
    for quoted in re.findall(r'["\']([^"\']{2,40})["\']', text):
        keywords.extend(quoted.lower().split())

    # File paths and extensions (e.g. cortex.py, /home/akien/foo.txt)
    for token in re.findall(r"\b\w+\.\w{1,6}\b", text):
        keywords.append(token.lower())

    # Version numbers (e.g. v3.2, 14B, Q4_K_M)
    for token in re.findall(r"\b(?:v?\d+[\._]\d[\w\.]*|\d+[Bb])\b", text):
        keywords.append(token.lower())

    # camelCase / PascalCase — split and add both whole and parts
    for token in re.findall(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", text):
        keywords.append(token.lower())
        for part in re.findall(r"[A-Z][a-z]+", token):
            keywords.append(part.lower())

    # Standard words (alpha, 3+ chars), respecting stop words + whitelist
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    keywords.extend(
        w for w in words if w not in stop_words or w in PROPER_NOUN_WHITELIST
    )

    # Deduplicate preserving order
    seen: set = set()
    result = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result


def _classify_intent(text: str, keywords: list) -> str:
    """
    #93 phase 1 / G36: Expanded 13-intent taxonomy.
    Intents: greeting | meta_question | explanation_request | factual_question |
             memory_instruction | action_request | code_task | analysis_task |
             complaint | conversation | command | creative_request | general
    """
    t = text.lower()

    if t.startswith("/"):
        return "command"
    if re.search(r"\b(hello|hey|hi|howdy)\b", t) or any(
        w in t for w in ["good morning", "good evening"]
    ):
        return "greeting"
    if any(
        w in t
        for w in [
            "how do you work",
            "how are you",
            "what are you",
            "who are you",
            "what can you do",
            "tell me about yourself",
        ]
    ):
        return "meta_question"
    if any(
        w in t
        for w in [
            "remember that",
            "remember this",
            "save this",
            "note that",
            "learn that",
            "don't forget",
            "keep in mind",
        ]
    ):
        return "memory_instruction"
    if any(
        w in t
        for w in [
            "write code",
            "fix the code",
            "debug",
            "implement",
            "refactor",
            "function that",
            "class that",
            "script to",
            "patch",
        ]
    ):
        return "code_task"
    if re.search(r"\b(analyse|analyze|compare|summarize|summarise|audit)\b", t) or any(
        w in t for w in ["what patterns", "what trends", "review "]
    ):
        return "analysis_task"
    if any(
        w in t
        for w in [
            "why did you",
            "why are you",
            "explain",
            "reasoning",
            "how does",
            "walk me through",
        ]
    ):
        return "explanation_request"
    if any(
        w in t
        for w in [
            "capital of",
            "what is",
            "what's",
            "tell me about",
            "who invented",
            "when did",
            "where is",
        ]
    ):
        return "factual_question"
    # G36: creative/reading requests — checked BEFORE action_request so "start reading",
    # "start at chapter" etc. don't get swallowed by the "start " action pattern.
    if any(
        w in t
        for w in [
            "read me",
            "read to me",
            "tell me a story",
            "write me a poem",
            "write me a story",
            "let's read",
            "read aloud",
            "narrate",
            "sing me",
            "recite",
            "read through",
            # reading session patterns — must stay foreground
            "start at chapter",
            "start reading",
            "reading each sentence",
            "read each sentence",
            "let it sit",
            "we talk about it",
            "then we talk",
            "then we discuss",
            "your assessment",
            "chapter by chapter",
            "read together",
            "reading together",
            "sentence by sentence",
        ]
    ):
        return "creative_request"
    if any(
        w in t
        for w in [
            "run ",
            "execute",
            "search for",
            "find ",
            "browse",
            "open ",
            "launch ",
            "stop ",
            "restart",
        ]
    ):
        return "action_request"
    if any(
        w in t
        for w in [
            "broken",
            "not working",
            "wrong",
            "fail",
            "error",
            "bug",
            "doesn't work",
            "can't ",
            "won't ",
            "never ",
        ]
    ):
        return "complaint"
    if any(
        w in t
        for w in [
            "think",
            "feel",
            "opinion",
            "thoughts on",
            "what do you reckon",
            "agree",
            "disagree",
            "interesting",
        ]
    ):
        return "conversation"
    return "general"


def _assess_complexity(text: str, keywords: list) -> str:
    """
    #93 phase 1: Heuristic complexity classification for tier skip_to.
    high  → suggest skip_to tier.4 (multi-step, multi-tool, technical depth)
    low   → tier.2/3 sufficient (single-fact, greeting, simple command)
    medium → default
    """
    t = text.lower()
    word_count = len(text.split())

    # High-complexity signals
    high_signals = 0
    if word_count > 40:
        high_signals += 1
    if any(
        w in t
        for w in [
            "and then",
            "after that",
            "first ",
            "second ",
            "finally ",
            "step by step",
            "multiple",
            "several",
        ]
    ):
        high_signals += 1  # multi-step
    if any(
        w in t
        for w in [
            "compare",
            "analyse",
            "analyze",
            "audit",
            "review",
            "refactor",
            "implement",
            "debug",
            "architect",
        ]
    ):
        high_signals += 1  # analytical depth
    if len([k for k in keywords if "." in k or any(c.isupper() for c in k)]) >= 3:
        high_signals += 1  # dense technical tokens
    if high_signals >= 2:
        return "high"

    # Low-complexity signals
    if word_count <= 6:
        return "low"
    if t.startswith("/"):
        return "low"
    if any(
        w in t for w in ["hello", "hi ", "hey ", "thanks", "ok ", "yes", "no ", "sure"]
    ):
        return "low"

    return "medium"


_STATUS_PHRASES = (
    "what are you doing",
    "what are you working on",
    "what tier",
    "which tier",
    "what model are you",
    "are you local",
    "is cloud available",
    "how much have you spent",
    "session cost",
    "how many memories",
    "how many habits",
    "what time is it",
    "what's the time",
    "what day is it",
    "what's today",
    "today's date",
    "what is today",
)

_HELP_PHRASES = (
    "what commands",
    "what can you do",
    "list commands",
    "what habits do you have",
    "list habits",
    "what tools do you have",
    "list tools",
)

_MEMORY_LOOKUP_PHRASES = (
    "do you remember",
    "what do you know about",
    "did you save",
    "did you get that",
    "was that saved",
    "did you store",
    "what's my name",
    "what is my name",
    "who am i",
    "what have you saved",
    "what's in my notebook",
)


def _assess_output_complexity(text: str, intent: str) -> str:
    """
    #154 / #156: Classify how complex the *output* needs to be.

    "low" → a Python template can satisfy this without any LLM.
    Pure acks, simple greetings, status queries, and help queries qualify.
    Everything else is "medium" (default) — don't risk wrong short answers.
    """
    t = text.lower().strip().rstrip("!.?")
    word_count = len(t.split())

    # Commands never get tier.0 (they have structured outputs)
    if t.startswith("/"):
        return "medium"

    # Pure short acks / affirmations (≤4 words)
    _acks = {
        "ok",
        "okay",
        "yes",
        "yeah",
        "yep",
        "yup",
        "no",
        "nope",
        "sure",
        "got it",
        "understood",
        "noted",
        "thanks",
        "thank you",
        "cheers",
        "cool",
        "great",
        "nice",
        "perfect",
        "good",
        "alright",
        "right",
        "fine",
        "k",
    }
    if word_count <= 4 and t in _acks:
        return "low"

    # Greetings
    if intent == "greeting" and word_count <= 6:
        return "low"

    # Status introspection queries
    if any(p in t for p in _STATUS_PHRASES):
        return "low"

    # Help / capability queries
    if any(p in t for p in _HELP_PHRASES):
        return "low"

    # Memory lookups / confirmation echoes
    if any(p in t for p in _MEMORY_LOOKUP_PHRASES):
        return "low"

    return "medium"


# #182: Fundamental traversal direction for each strategy.
# up   — "why?" / causal_trace / lever_trace: upward causal trace toward convergence
# down — "how?" / semantic_depth / factual_leaf: downward mechanism or detail trace
# lateral — "what fits?" / broad_search: sibling search for gap-filling candidates
# lookup — memory_verify / attractor_hold: not pure traversal; search + surface
_STRATEGY_DIRECTION: dict[str, str] = {
    "causal_trace": "up",
    "lever_trace": "up",
    "semantic_depth": "down",
    "factual_leaf": "down",
    "broad_search": "lateral",
    "memory_verify": "lookup",
    "attractor_hold": "lookup",
}


def _classify_question_traversal(text: str, intent: str) -> tuple[str, str]:
    """
    #181: Questions as traversal programs.

    Question FORM determines traversal STRATEGY and ENTRY POINT.
    Returns (traversal_strategy, traversal_entry).

    Strategies:
      semantic_depth  — deep traversal from semantic anchor (how/explain/walk)
      causal_trace    — causal direction (why/what caused/how did it)
      broad_search    — broad BFS from multiple entries (what fits/what would)
      factual_leaf    — traverse to nearest factual node (what is/when/where)
      memory_verify   — search + ring verification (do you remember/did you save)
      attractor_hold  — traverse weighted toward TWM attractor (opinion/think)
      (empty)         — no special traversal hint (non-question turns)

    Entry points:
      semantic_anchor — extract topic from keywords, anchor traversal there
      cp_closest      — find nearest core pattern to the topic
      twm_attractor   — use whatever is in TWM attractor as seed
      ring_recent     — start from most recent ring entries
      (empty)         — use default CP1-CP6 seeds
    """
    t = text.lower().strip()

    # Not a question at all — no traversal hint
    if intent in ("command", "memory_instruction", "code_task") or not any(
        c in t
        for c in ("?", "how", "why", "what", "when", "where", "which", "who", "explain")
    ):
        return "", ""

    # Memory verification — "do you remember", "did you save/get/store"
    if any(
        p in t
        for p in (
            "do you remember",
            "did you save",
            "did you get",
            "did you store",
            "was that saved",
            "what have you saved",
            "what do you know about",
            "what's my name",
            "who am i",
        )
    ):
        return "memory_verify", "ring_recent"

    # Causal trace — "why", "what caused", "how did X happen"
    if any(
        p in t
        for p in (
            "why did",
            "why does",
            "why is",
            "why are",
            "what caused",
            "how did",
            "what made",
            "what led to",
            "because of",
        )
    ):
        return "causal_trace", "semantic_anchor"

    # Semantic depth — "how does X work", "explain", "walk me through"
    if any(
        p in t
        for p in (
            "how does",
            "how do",
            "how must",
            "explain",
            "walk me through",
            "tell me about",
            "how is it",
            "what is it like",
            "how would",
        )
    ):
        return "semantic_depth", "semantic_anchor"

    # Broad search — "what would fit", "what could", "what are options"
    if any(
        p in t
        for p in (
            "what would",
            "what could",
            "what fits",
            "what else",
            "what options",
            "what possibilities",
            "what if",
            "how might",
            "what might",
        )
    ):
        return "broad_search", "twm_attractor"

    # Opinion/reflection — "what do you think", "what's your take"
    if any(
        p in t
        for p in (
            "what do you think",
            "what do you feel",
            "what's your",
            "your opinion",
            "your thoughts",
            "do you agree",
            "how do you feel",
            "what do you reckon",
        )
    ):
        return "attractor_hold", "twm_attractor"

    # Lever trace — "where's the leverage?", "what's the key variable?", "what's driving this?"
    # Checked BEFORE factual_leaf — "what's the key" must not fall through to factual_leaf.
    # Upward causal trace that terminates at convergence nodes (investment_weight or out_degree)
    if any(
        p in t
        for p in (
            "where's the lever",
            "where is the lever",
            "what's the key",
            "what is the key",
            "what's driving",
            "what is driving",
            "what's really",
            "what would change everything",
            "root cause",
            "what's the cause",
            "what is the cause",
            "fundamental",
            "what's the bottleneck",
            "what's blocking",
            "highest leverage",
        )
    ):
        return "lever_trace", "semantic_anchor"

    # Factual leaf — "what is", "when did", "where is", "who is"
    if any(
        p in t
        for p in (
            "what is",
            "what are",
            "what's",
            "when did",
            "when is",
            "where is",
            "where are",
            "who is",
            "who are",
            "which is",
        )
    ):
        return "factual_leaf", "cp_closest"

    # Default for question-shaped turns without a clear form
    if "?" in t:
        return "semantic_depth", "semantic_anchor"

    return "", ""


def _detect_tone(text: str) -> str:
    text_lower = text.lower()
    # #77: urgent requires explicit urgency words — bare "!" no longer qualifies
    if any(
        w in text_lower
        for w in ["urgent", "asap", "immediately", "right now", "emergency"]
    ):
        return "urgent"
    if any(
        w in text_lower
        for w in ["frustrated", "annoyed", "wrong", "broken", "stupid", "not working"]
    ):
        return "frustrated"
    if any(
        w in text_lower
        for w in [
            "hello",
            "hi ",
            "hey",
            "thanks",
            "please",
            "great",
            "nice",
            "good work",
        ]
    ):
        return "friendly"
    if any(w in text_lower for w in ["?", "how", "why", "what", "curious", "explain"]):
        return "curious"
    return "neutral"
