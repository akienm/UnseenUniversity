"""Tool + ToolRegistry + ToolStats — self-contained, no lab.utility_closet dependency.

Migrated from lab/utility_closet/registry.py into UU so all devices can
import without depending on the TheIgors repo. The AgentBase dependency
now comes from the local agent_base module.

Tools are AI-agnostic: they describe their parameters once, and adapter
methods (to_anthropic_schema / to_openai_schema / to_text_description)
convert to whichever protocol the caller's reasoner speaks. The registry
is a process-singleton (`registry`) — tool modules self-register on import.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .agent_base import AgentBase, get_logger

_log = get_logger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the parameters
    fn: Callable

    def execute(self, **kwargs) -> str:
        return self.fn(**kwargs)

    def to_anthropic_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai_schema(self) -> dict:
        params = self.parameters
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
        props = self.parameters.get("properties", {})
        params = ", ".join(
            f"{k}: {v.get('description', v.get('type', ''))}" for k, v in props.items()
        )
        return f"{self.name}({params})\n  {self.description}"


@dataclass
class ToolStats:
    """Per-tool call statistics. Keeps newest-1000 latency samples for percentiles."""

    call_count: int = 0
    error_count: int = 0
    _samples: deque = field(default_factory=lambda: deque(maxlen=1000))

    _MAX_SAMPLES = 1000

    def record(self, elapsed_ms: int, success: bool) -> None:
        self.call_count += 1
        if not success:
            self.error_count += 1
        self._samples.append(elapsed_ms)

    @property
    def error_rate(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.error_count / self.call_count

    def _pct(self, p: int) -> int | None:
        if not self._samples:
            return None
        ordered = sorted(self._samples)
        idx = max(0, int(len(ordered) * p / 100) - 1)
        return ordered[idx]

    @property
    def p50(self) -> int | None:
        return self._pct(50)

    @property
    def p95(self) -> int | None:
        return self._pct(95)

    def to_dict(self) -> dict:
        return {
            "calls": self.call_count,
            "errors": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "p50_ms": self.p50,
            "p95_ms": self.p95,
        }


class ToolRegistry(AgentBase):
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._stats: dict[str, ToolStats] = {}

    def register(self, tool: "Tool"):
        self._tools[tool.name] = tool
        return tool

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
        t0 = time.perf_counter()
        try:
            result = tool.execute(**kwargs)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            success = not str(result).startswith("Error")
            self._record(name, elapsed_ms, success)
            return result
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._record(name, elapsed_ms, False)
            self._record_misfire(name, type(e).__name__)
            return f"Error executing {name}: {e}"

    def _record(self, name: str, elapsed_ms: int, success: bool) -> None:
        if name not in self._stats:
            self._stats[name] = ToolStats()
        self._stats[name].record(elapsed_ms, success)

    def _record_misfire(self, tool_name: str, error_type: str) -> None:
        try:
            from devices.igor.tools.misfire_counter import get_misfire_counter

            counter = get_misfire_counter()
            counter.record_tool_error(
                tool_name, error_type, dispatch_path="tool_execute"
            )
        except Exception as e:
            _log.debug("Failed to record misfire for %s: %s", tool_name, e)

    def tool_stats(self) -> dict[str, dict]:
        return {
            k: v.to_dict()
            for k, v in sorted(
                self._stats.items(), key=lambda x: x[1].call_count, reverse=True
            )
        }

    def to_anthropic_schemas(self) -> list[dict]:
        return [t.to_anthropic_schema() for t in self.all()]

    def to_openai_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self.all()]

    def to_text_descriptions(self) -> str:
        return "\n\n".join(t.to_text_description() for t in self.all())


# Global registry — tools register themselves on import
registry = ToolRegistry()
