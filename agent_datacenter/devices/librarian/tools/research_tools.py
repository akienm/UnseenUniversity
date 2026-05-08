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
            "depth: 'shallow' (direct answer) or 'deep' (structured, thorough)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Question or topic to research",
                },
                "depth": {
                    "type": "string",
                    "enum": ["shallow", "deep"],
                    "description": "Research depth (default: shallow)",
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


def research(query: str, depth: str = "shallow") -> str:
    result = _engine().research(query, depth=depth)
    return json.dumps(
        {
            "answer": result.answer,
            "query": result.query,
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
        return research(args["query"], args.get("depth", "shallow"))
    if name == "build_summary":
        return build_summary(args["topic"])
    return None
