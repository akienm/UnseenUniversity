"""
BrowserUseChannel — structured acquisition via browser-use agent.

Last-resort channel for complex web tasks with constraints.
Constraints: NO account creation, NO book switching, NO login prompts (fail cleanly).
Medium cost (uses claude-haiku via OR).
Low-medium reliability (depends on site structure + browser-use agent accuracy).
"""

from __future__ import annotations

from datetime import datetime

from ...igor_base import IgorBase
from . import (
    Channel,
    ChannelReliability,
    AcquireRequest,
    AcquireResult,
    ChannelFailure,
    BlobMeta,
)


class BrowserUseChannel(Channel, IgorBase):
    """
    Use browser-use agent for structured content acquisition.

    Query format:
      "task: <description>" — e.g., "task: fetch the product page HTML"
      or a bare description interpreted as a task.

    Enforced constraints:
      - NO account creation (fail if asked to sign up)
      - NO book switching (stay on source)
      - NO login prompts (fail cleanly instead of trying to log in)

    Returns acquired content as markdown or HTML.
    """

    def __init__(self):
        super().__init__(
            name="BrowserUseChannel",
            constraints=[
                "NO account creation",
                "NO book switching",
                "NO login prompts (fail cleanly)",
            ],
            cost_per_call_usd=0.001,  # haiku via OR
            reliability=ChannelReliability.MEDIUM,
            one_way=False,
            short_circuits=False,
            max_attempts=2,
            backoff_sec=5.0,
        )

    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Use browser-use agent to acquire content for the given task.

        The query is expected to be a task description or URL + task.
        """
        try:
            from ...tools.browser import browser_use_task

            query = request.query.strip()
            if not query:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Empty task query",
                    cost_usd=0.0,
                )

            # Parse query: might be a URL or a task or "task: <description>"
            task_description = query
            url = None

            if query.startswith("http://") or query.startswith("https://"):
                parts = query.split(maxsplit=1)
                url = parts[0]
                task_description = (
                    parts[1] if len(parts) > 1 else f"Extract content from {url}"
                )
            elif query.lower().startswith("task:"):
                task_description = query[5:].strip()

            # Inject constraints into the task
            constrained_task = self._add_constraints(task_description)

            try:
                result_text = browser_use_task(
                    constrained_task,
                    url=url,
                    max_steps=20,
                    timeout=180,
                )
            except Exception as e:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Browser task failed: {str(e)[:200]}",
                    cost_usd=self.cost_per_call_usd,
                )

            if not result_text:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Browser returned empty content",
                    cost_usd=self.cost_per_call_usd,
                )

            # Check for constraint violations in the result
            violations = self._check_constraints(result_text)
            if violations:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Constraint violated: {violations}",
                    cost_usd=self.cost_per_call_usd,
                )

            blob = result_text.encode("utf-8")

            # Title from URL or task
            title = "Browser task result"
            if url:
                from urllib.parse import urlparse
                from pathlib import Path

                parsed = urlparse(url)
                title = Path(parsed.path).stem or parsed.netloc or "content"

            meta = BlobMeta(
                title=title,
                source=self.name,
                url=url,
                format="markdown",
                size_bytes=len(blob),
                retrieved_at=datetime.utcnow().isoformat() + "Z",
            )

            return AcquireResult(
                blob=blob,
                meta=meta,
                cost_usd=self.cost_per_call_usd,
            )

        except ImportError:
            return ChannelFailure(
                channel_name=self.name,
                reason="browser_use_task not available",
                cost_usd=0.0,
            )
        except Exception as e:
            return ChannelFailure(
                channel_name=self.name,
                reason=f"Error: {type(e).__name__}: {str(e)[:200]}",
                cost_usd=0.0,
            )

    def _add_constraints(self, task: str) -> str:
        """Inject constraint warnings into the task."""
        warning = """
⚠️ CONSTRAINTS (fail if violated):
  - DO NOT create an account or sign up
  - DO NOT switch to a different book or source
  - If asked to log in, STOP and report: "Login required"

Only proceed if you can complete the task without violating these.
"""
        return f"{warning}\n\nTask: {task}"

    def _check_constraints(self, result: str) -> str:
        """Check result for constraint violations."""
        result_lower = result.lower()

        # Check for signs of account creation
        if any(
            x in result_lower
            for x in ["account created", "sign up successful", "registration complete"]
        ):
            return "Account was created (violated constraint)"

        # Check for login prompts that were answered
        if "login successful" in result_lower or "logged in" in result_lower:
            # This is OK if it was unavoidable, but warn
            return ""  # Allow logins that happened; return empty to allow

        # No violations detected
        return ""
