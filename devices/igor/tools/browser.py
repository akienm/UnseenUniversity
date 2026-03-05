"""
Browser Use tool - AI-driven web browser automation via browser-use library.

Allows Igor to navigate websites, interact with pages, extract information,
and perform browser-based tasks using natural language task descriptions.

Uses browser_use.llm.ChatAnthropic (or ChatOpenRouter) as the LLM backend.
Runs async Agent tasks in a synchronous wrapper.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from .registry import Tool, registry

# Configure logging
logger = logging.getLogger(__name__)
LOG_DIR = Path.home() / ".TheIgors" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_LOG_PATH = LOG_DIR / "browser_use.log"


def _init_browser_log():
    """Initialize file logging for browser operations."""
    handler = logging.FileHandler(BROWSER_LOG_PATH)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logging.getLogger("browser_use").addHandler(handler)
    logging.getLogger("browser_use").setLevel(logging.INFO)


_init_browser_log()


def _make_llm():
    """
    Create an LLM for the browser agent.
    Prefers OpenRouter (cost control), falls back to Anthropic direct.
    """
    from browser_use.llm import ChatOpenRouter, ChatAnthropic

    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if or_key:
        return ChatOpenRouter(
            model="anthropic/claude-sonnet-4-6",
            api_key=or_key,
        )
    return ChatAnthropic(model="claude-haiku-4-5-20251001")


def browser_use_task(
    task_description: str,
    url: Optional[str] = None,
    max_steps: int = 10,
    timeout: int = 120,
) -> str:
    """
    Execute a browser automation task using AI-driven browser control.

    Args:
        task_description: Natural language description of what to do
            (e.g., "Go to Gemini and ask it about neuroscience")
        url: Optional starting URL. If None, opens blank page
        max_steps: Maximum number of browser actions to attempt (safety limit)
        timeout: Max seconds to wait for task completion

    Returns:
        JSON string with result status, extracted data, and any errors
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_browser_agent(
                    task_description,
                    url=url,
                    max_steps=max_steps,
                    timeout=timeout,
                )
            )
            return json.dumps(result, indent=2)
        finally:
            loop.close()

    except ImportError as e:
        return json.dumps({
            "status": "error",
            "error": f"Browser-use library not available: {e}",
            "details": "Ensure browser-use is installed: pip install browser-use",
        })
    except Exception as e:
        logger.exception("Browser task failed")
        return json.dumps({
            "status": "error",
            "error": str(e),
            "task": task_description,
        })


async def _run_browser_agent(
    task_description: str,
    url: Optional[str] = None,
    max_steps: int = 10,
    timeout: int = 120,
) -> dict:
    """
    Async implementation of browser agent task execution.

    Returns dict with:
        - status: 'success' | 'timeout' | 'error'
        - result: extracted/final data
        - final_url: URL after task completion
        - steps_taken: number of browser actions
    """
    from browser_use import Agent

    steps_taken = 0
    final_url = url or "about:blank"
    history = []

    async def on_step(state, output, step_num):
        nonlocal steps_taken, final_url
        steps_taken = step_num
        try:
            if hasattr(state, "url") and state.url:
                final_url = state.url
        except Exception:
            pass
        history.append({
            "step": step_num,
            "output": str(output)[:500],
        })
        if step_num >= max_steps:
            raise RuntimeError(f"Exceeded max_steps limit: {max_steps}")

    full_task = (
        f"{task_description}\n\n"
        "Safety constraints:\n"
        "1. Do not make purchases or enter credit card information\n"
        "2. Do not submit forms without explicit confirmation\n"
        "3. Respect robots.txt and site terms of service\n"
        "4. Report extracted data back clearly when done"
    )

    initial_actions = None
    if url and url not in ("about:blank", ""):
        # browser_use 0.11.x renamed go_to_url → navigate
        initial_actions = [{"navigate": {"url": url}}]

    try:
        agent = Agent(
            task=full_task,
            llm=_make_llm(),
            use_vision=True,
            max_actions_per_step=1,
            step_timeout=timeout,
            max_failures=3,
            initial_actions=initial_actions,
            register_new_step_callback=on_step,
        )

        logger.info(f"Browser task started: {task_description[:100]}...")
        result = await agent.run()

        # Extract final URL
        try:
            final_url = result.final_state().url or final_url
        except Exception:
            pass

        # Extract result text
        extracted = None
        try:
            extracted = result.final_result()
        except Exception:
            pass
        if not extracted:
            try:
                extracted = str(result)[:1000]
            except Exception:
                extracted = "Task completed"

        logger.info(f"Browser task completed: {steps_taken} steps")
        return {
            "status": "success",
            "result": extracted,
            "final_url": final_url,
            "steps_taken": steps_taken,
            "history": history[-3:] if history else [],
        }

    except asyncio.TimeoutError:
        logger.warning(f"Browser task timeout after {steps_taken} steps")
        return {
            "status": "timeout",
            "error": f"Task exceeded {timeout}s timeout",
            "steps_taken": steps_taken,
            "final_url": final_url,
        }
    except Exception as e:
        logger.exception(f"Browser agent error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "steps_taken": steps_taken,
            "final_url": final_url,
        }


# ── Register tool ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="browser_use_task",
    description=(
        "Execute a browser automation task using AI-driven control. "
        "Describe what you want done (navigate sites, extract data, interact with pages, "
        "use web services like Gemini), and the browser agent will perform the task. "
        "Returns extracted data or confirmation of completion."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": (
                    "Natural language description of the task. "
                    "E.g., 'Go to Gemini and ask about cognitive architectures'"
                ),
            },
            "url": {
                "type": "string",
                "description": "Optional starting URL (e.g., 'https://gemini.google.com')",
            },
            "max_steps": {
                "type": "integer",
                "description": "Maximum browser actions to attempt (default 10, max 50)",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default 120)",
            },
        },
        "required": ["task_description"],
    },
    fn=browser_use_task,
))
