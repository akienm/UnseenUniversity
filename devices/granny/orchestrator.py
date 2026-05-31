"""
GrannyPatternOrchestrator — factory orchestrator using Granny's routing pattern.

Extracted structural pattern: one agent holds the factory goal, routes tasks
to member agents, escalates to owner when blocked.

Routing uses the same priority order as GrannyWeatherwaxDevice:
  1. Explicit agent_type preference
  2. Keyword match (agent_type name words vs task description)
  3. First available member
  4. Escalate to owner when no members available

Channel posting and escalation use the same pattern as GrannyWeatherwaxDevice
so behaviour is consistent across the rack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Data types ──────────────────────────────────────────────────────────────────


@dataclass
class FactoryTask:
    """A task routed through a factory's orchestrator."""

    task_id: str
    description: str
    preferred_agent_type: str | None = None


@dataclass
class RoutingResult:
    routed: bool
    agent_type: str | None
    comms_address: str | None
    reason: str = ""


# ── Orchestrator ────────────────────────────────────────────────────────────────


class GrannyPatternOrchestrator:
    """
    Factory orchestrator using Granny's routing pattern.

    Holds factory_id, owner_id, and the member address map.
    Routes tasks to members; escalates to owner_id when blocked.
    """

    def __init__(
        self,
        factory_id: str,
        owner_id: str,
        member_addresses: dict[str, str],  # {agent_type: comms_address}
        channel_post_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self._factory_id = factory_id
        self._owner_id = owner_id
        self._members: dict[str, str] = dict(member_addresses)
        self._post = channel_post_fn or _default_post
        self._goal: str | None = None

    def receive_goal(self, goal: str) -> None:
        """Accept a factory goal and notify owner."""
        self._goal = goal
        self._post(
            self._owner_id,
            f"FACTORY_GOAL_RECEIVED|factory={self._factory_id}|goal={goal[:120]}",
        )
        log.info("factory %s received goal: %s", self._factory_id, goal[:80])

    def route_task(self, task: FactoryTask) -> RoutingResult:
        """Route a factory task to the best-matched member.

        Priority:
          1. task.preferred_agent_type (if registered)
          2. Keyword match: agent_type words found in task description
          3. First available member
          4. Escalate to owner when members dict is empty
        """
        if not self._members:
            self.escalate(f"no members registered for factory {self._factory_id}")
            return RoutingResult(
                routed=False,
                agent_type=None,
                comms_address=None,
                reason="no_members",
            )

        # 1. Explicit preference
        preferred = task.preferred_agent_type
        if preferred and preferred in self._members:
            addr = self._members[preferred]
            self._post(
                addr,
                (
                    f"FACTORY_TASK"
                    f"|factory={self._factory_id}"
                    f"|task_id={task.task_id}"
                    f"|desc={task.description[:120]}"
                ),
            )
            log.info(
                "factory %s routed %s → %s (explicit)",
                self._factory_id,
                task.task_id,
                preferred,
            )
            return RoutingResult(
                routed=True,
                agent_type=preferred,
                comms_address=addr,
                reason="explicit_preference",
            )

        # 2. Keyword match — split agent_type on hyphens/underscores, check description
        desc_lower = task.description.lower()
        for agent_type, addr in self._members.items():
            keywords = agent_type.replace("-", " ").replace("_", " ").split()
            if any(kw in desc_lower for kw in keywords):
                self._post(
                    addr,
                    (
                        f"FACTORY_TASK"
                        f"|factory={self._factory_id}"
                        f"|task_id={task.task_id}"
                        f"|desc={task.description[:120]}"
                    ),
                )
                log.info(
                    "factory %s routed %s → %s (keyword)",
                    self._factory_id,
                    task.task_id,
                    agent_type,
                )
                return RoutingResult(
                    routed=True,
                    agent_type=agent_type,
                    comms_address=addr,
                    reason="keyword_match",
                )

        # 3. First available member
        agent_type, addr = next(iter(self._members.items()))
        self._post(
            addr,
            (
                f"FACTORY_TASK"
                f"|factory={self._factory_id}"
                f"|task_id={task.task_id}"
                f"|desc={task.description[:120]}"
            ),
        )
        log.info(
            "factory %s routed %s → %s (first available)",
            self._factory_id,
            task.task_id,
            agent_type,
        )
        return RoutingResult(
            routed=True,
            agent_type=agent_type,
            comms_address=addr,
            reason="first_available",
        )

    def escalate(self, reason: str) -> None:
        """Post escalation to owner_id — same pattern as Granny.escalate_to_cc."""
        self._post(
            self._owner_id,
            f"FACTORY_ESCALATE|factory={self._factory_id}|reason={reason}",
        )
        log.info("factory %s escalated to owner: %s", self._factory_id, reason)

    def health_summary(self, member_health: dict[str, str]) -> dict:
        """Aggregate member health statuses into a factory-level summary."""
        overall = (
            "healthy"
            if all(h in ("healthy", "unknown") for h in member_health.values())
            else "degraded"
        )
        return {
            "factory_id": self._factory_id,
            "overall": overall,
            "members": member_health,
            "checked_at": _now_iso(),
        }


# ── Default channel post ────────────────────────────────────────────────────────


def _default_post(address: str, message: str) -> None:
    """Post to the rack channel. Gracefully no-ops when channel is unavailable."""
    try:
        from unseen_university.channel import post_to_channel

        post_to_channel(message, author="factory-orchestrator", channel=address)
    except Exception as exc:
        log.warning("orchestrator channel post failed (%s): %s", address, exc)
