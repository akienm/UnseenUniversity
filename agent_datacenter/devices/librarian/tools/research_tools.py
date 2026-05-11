"""Research and summarization MCP tools — backed by ResearchEngine."""

from __future__ import annotations

import json

SCHEMAS = [
    {
        "name": "summarize",
        "description": (
            "Summarize text using the Librarian's tier-1 (heavy) model. "
            "style: 'brief' (2-3 sentences), 'detailed' (full coverage), 'bullets' (key points list)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to summarize"},
                "style": {
                    "type": "string",
                    "enum": ["brief", "detailed", "bullets"],
                    "description": "Summary style (default: brief)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "research",
        "description": (
            "Research a question or topic using the Librarian. "
            "breadth 0.0-1.0: how wide to cast (0=single focused source, 1=broad survey). "
            "depth 0.0-1.0: how deep per source (0=2-3 sentence summary, 1=full synthesis). "
            "Canonical two-pass pattern: breadth=0.8,depth=0.2 for landscape survey; "
            "breadth=0.1,depth=0.9 for deep synthesis on a specific target."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Question or topic to research",
                },
                "breadth": {
                    "type": "number",
                    "description": "How wide to cast: 0.0 (single focused) to 1.0 (broad survey). Default 0.5.",
                },
                "depth": {
                    "type": "number",
                    "description": "How deep per source: 0.0 (2-3 sentences) to 1.0 (full synthesis). Default 0.5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "build_summary",
        "description": (
            "Build a summary for a topic or ticket ID (e.g. 'T-foo' or 'IMAP IDLE'). "
            "Returns a brief summary of what is known about the topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic or ticket ID to summarize",
                },
            },
            "required": ["topic"],
        },
    },
]


def _engine():
    from agent_datacenter.devices.librarian.research import ResearchEngine

    return ResearchEngine()


def summarize(text: str, style: str = "brief") -> str:
    result = _engine().summarize(text, style=style)
    return json.dumps(
        {
            "summary": result.text,
            "style": result.style,
            "model": result.model,
            "tier": result.tier,
        },
        default=str,
    )


def research(query: str, breadth: float = 0.5, depth: float = 0.5) -> str:
    result = _engine().research(query, breadth=breadth, depth=depth)
    return json.dumps(
        {
            "answer": result.answer,
            "query": result.query,
            "breadth": result.breadth,
            "depth": result.depth,
            "model": result.model,
            "tier": result.tier,
            "sources": result.sources,
        },
        default=str,
    )


def build_summary(topic: str) -> str:
    result = _engine().build_summary(topic)
    return json.dumps(
        {
            "summary": result.text,
            "topic": topic,
            "model": result.model,
            "tier": result.tier,
        },
        default=str,
    )


def dispatch(name: str, args: dict) -> str | None:
    if name == "summarize":
        return summarize(args["text"], args.get("style", "brief"))
    if name == "research":
        return research(args["query"], args.get("breadth", 0.5), args.get("depth", 0.5))
    if name == "build_summary":
        return build_summary(args["topic"])
    return None
