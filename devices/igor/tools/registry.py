"""
Tool registry - AI-agnostic tool definitions.
Tools know nothing about which AI calls them.
Reasoner adapters convert these to whatever protocol the AI speaks.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the parameters
    fn: Callable

    def execute(self, **kwargs) -> str:
        return self.fn(**kwargs)

    def to_anthropic_schema(self) -> dict:
        """Convert to Anthropic tool_use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling format."""
        params = self.parameters
        # Normalize shorthand {param: {type, desc}} to full JSON Schema object.
        # OpenAI/gpt-4o-mini rejects schemas missing "type": "object" at top level.
        if params.get("type") != "object" and "properties" not in params:
            params = {
                "type": "object",
                "properties": params,
                "required": [],
            }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    def to_text_description(self) -> str:
        """Plain text description for browser-based / text-only reasoners."""
        props = self.parameters.get("properties", {})
        params = ", ".join(
            f"{k}: {v.get('description', v.get('type', ''))}" for k, v in props.items()
        )
        return f"{self.name}({params})\n  {self.description}"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: "Tool"):
        self._tools[tool.name] = tool
        return tool  # allows use as decorator (if tool is already an instance)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def execute(self, name: str, kwargs: dict) -> str:
        tool = self.get(name)
        if not tool:
            available = ", ".join(sorted(self._tools.keys()))
            return (
                f"Error: Unknown tool '{name}'. "
                f"Do not retry — use one of the available tools instead: {available}"
            )
        try:
            return tool.execute(**kwargs)
        except Exception as e:
            return f"Error executing {name}: {e}"

    def to_anthropic_schemas(self) -> list[dict]:
        return [t.to_anthropic_schema() for t in self.all()]

    def to_openai_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self.all()]

    def to_text_descriptions(self) -> str:
        """All tools described in plain text - for non-API reasoners."""
        return "\n\n".join(t.to_text_description() for t in self.all())


# Global registry - tools register themselves on import
registry = ToolRegistry()
