"""
LocalOllamaPool — distributes reasoning across available local-network Ollama instances.

Reads ~/.TheIgors/local/machines.csv to discover online machines, creates an
OllamaReasoner for each, and round-robins requests across them. Falls back to
the local instance if all remotes fail.

Machine selection: machines with a valid IP (not "OFFLINE") are treated as candidates.
Own machine (akiendelllinux / 127.0.0.1) is always included as the last fallback.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterator

from .reasoners.ollama_reasoner import OllamaReasoner, DEFAULT_MODEL
from ..memory.models import Memory

MACHINES_CSV = Path.home() / ".TheIgors" / "local" / "machines.csv"
OLLAMA_PORT  = 11434


def _parse_online_machines() -> list[dict]:
    """Parse machines.csv and return list of online-machine dicts."""
    if not MACHINES_CSV.exists():
        return []
    try:
        text = MACHINES_CSV.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        machines = []
        for row in reader:
            # Strip whitespace from all values
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            ip = row.get("IP", "").strip()
            if ip and ip.upper() != "OFFLINE":
                machines.append(row)
        return machines
    except Exception:
        return []


class LocalOllamaPool:
    """
    Round-robin pool of OllamaReasoner instances across network machines.

    Usage:
        pool = LocalOllamaPool(model="llama3.2:1b")
        response, cost = pool.reason(user_input, memories, core_patterns, instance_id)
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._reasoners: list[OllamaReasoner] = []
        self._index = 0
        self._refresh()

    def _refresh(self):
        """Rebuild reasoner list from machines.csv."""
        machines = _parse_online_machines()
        reasoners = []
        for m in machines:
            ip = m.get("IP", "")
            host = f"http://{ip}:{OLLAMA_PORT}"
            reasoners.append(OllamaReasoner(model=self.model, host=host))
        # Always include local as final fallback
        reasoners.append(OllamaReasoner(model=self.model, host=None))
        self._reasoners = reasoners
        self._index = 0

    def _next_reasoner(self) -> Iterator[OllamaReasoner]:
        """Yield each reasoner starting from current index, wrapping around."""
        n = len(self._reasoners)
        for i in range(n):
            yield self._reasoners[(self._index + i) % n]

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
    ) -> tuple[str, float]:
        """Try each machine in round-robin order; fall back on failure."""
        last_exc = None
        for reasoner in self._next_reasoner():
            try:
                result = reasoner.reason(user_input, relevant_memories, core_patterns, instance_id)
                # Advance index so next call goes to the next machine
                self._index = (self._index + 1) % len(self._reasoners)
                return result
            except Exception as exc:
                last_exc = exc
                continue  # Try next machine

        raise RuntimeError(
            f"All {len(self._reasoners)} Ollama instances failed. "
            f"Last error: {last_exc}"
        )

    def set_model(self, model: str) -> str:
        """Switch model on all pool members."""
        self.model = model
        for r in self._reasoners:
            r.model = model
        return model

    def machines_summary(self) -> str:
        """Human-readable list of pool members."""
        return ", ".join(r.name() for r in self._reasoners)
