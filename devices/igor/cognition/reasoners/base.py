"""
Base reasoner interface.
All reasoning adapters implement this - Anthropic API, browser-based AIs, local models.
Eventually: no upstream at all. Pure habit execution replaces reasoning entirely.
"""

from abc import ABC, abstractmethod
from ...memory.models import Memory


class BaseReasoner(ABC):
    """
    A reasoning adapter translates Igor's internal state into whatever
    protocol a specific AI speaks, executes the conversation, handles
    tool calls, and returns a plain text response.

    Igor doesn't care which reasoner is active. It calls reason() and
    gets text back.
    """

    @abstractmethod
    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
    ) -> tuple[str, float]:
        """
        Generate a response.
        Returns (response_text, cost_in_usd).
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this reasoner."""
        ...
