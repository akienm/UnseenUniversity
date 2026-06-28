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

from unseen_university.devices.igor.tools.registry import Tool, registry
from ..paths import paths
from ..cognition.forensic_logger import log_error

# Configure logging
logger = logging.getLogger(__name__)
LOG_DIR = paths().logs
LOG_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_LOG_PATH = LOG_DIR / "browser_use.log"


def _init_browser_log():
    """Initialize file logging for browser operations."""
    handler = logging.FileHandler(BROWSER_LOG_PATH)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger("browser_use").addHandler(handler)
    logging.getLogger("browser_use").setLevel(logging.INFO)


_init_browser_log()


def _make_llm():
    """
    Create an LLM for the browser agent.
    Always routes through OpenRouter — Igor never uses Anthropic direct.
    Default model: gpt-4o-mini (cheap; avoids Anthropic schema strictness bug
    in browser_use 0.11.x where 'minimum' on integer types causes 400 errors).
    Override with BROWSER_USE_MODEL env var (e.g. anthropic/claude-haiku-4-5).
    """
    from browser_use.llm import ChatOpenRouter

    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not or_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set — browser_use requires OpenRouter"
        )
    from ..cognition.inference_openrouter import OR_CHEAP_MODEL

    model = os.getenv("BROWSER_USE_MODEL", OR_CHEAP_MODEL)
    return ChatOpenRouter(model=model, api_key=or_key)


_virtual_display = None  # module-level singleton so it isn't GC'd


def _ensure_virtual_display():
    """
    Start a virtual display (Xvfb) when needed.

    IGOR_BROWSER_HEADLESS=true  → always use Xvfb (production default)
    IGOR_BROWSER_HEADLESS=false → use real display even when DISPLAY is set (debugging)
    unset                       → use real display if DISPLAY is set; Xvfb otherwise
    """
    headless_env = os.environ.get("IGOR_BROWSER_HEADLESS", "").lower()
    if headless_env == "false":
        return  # explicit debug mode — show on real display
    if headless_env != "true" and os.environ.get("DISPLAY"):
        return  # unset + real display present — show on screen (legacy debug behaviour)

    global _virtual_display
    if _virtual_display is not None:
        return
    try:
        from pyvirtualdisplay import Display

        _virtual_display = Display(visible=False, size=(1280, 900))
        _virtual_display.start()
        logger.info(
            f"Virtual display started (Xvfb) DISPLAY={os.environ.get('DISPLAY')} for browser_use"
        )
    except Exception as e:
        logger.warning(
            f"pyvirtualdisplay unavailable ({e}), browser may appear on real display"
        )


def browser_use_task(
    task: str,
    url: Optional[str] = None,
    max_steps: int = 30,
    timeout: int = 300,
) -> str:
    """
    Execute a browser automation task using AI-driven browser control.
    Uses Igor's own logged-in Chrome profile (chrome_igor_profile/Profile 1).

    Use for: reading Kindle ebooks (read.amazon.com), accessing Igor's logged-in
    accounts, general AI-driven web automation.

    Args:
        task: Natural language description of what to do
            (e.g., "Go to read.amazon.com and find Making Money by Pratchett")
        url: Optional starting URL. If None, opens blank page
        max_steps: Maximum number of browser actions to attempt (safety limit)
        timeout: Max seconds to wait for task completion

    Returns:
        JSON string with result status, extracted data, and any errors
    """
    _ensure_virtual_display()
    logger.info(
        f"browser_use_task: starting — task={task[:100]!r} url={url!r} max_steps={max_steps} timeout={timeout}"
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_browser_agent(
                    task,
                    url=url,
                    max_steps=max_steps,
                    timeout=timeout,
                )
            )
            logger.info(
                f"browser_use_task: completed status={result.get('status')} steps={result.get('steps_taken')}"
            )
            return json.dumps(result, indent=2)
        finally:
            loop.close()

    except ImportError as e:
        logger.error(f"browser_use_task: ImportError — {e}")
        return json.dumps(
            {
                "status": "error",
                "error": f"Browser-use library not available: {e}",
                "details": "Ensure browser-use is installed: pip install browser-use",
            }
        )
    except Exception as e:
        logger.exception(f"browser_use_task: unhandled exception — {e}")
        return json.dumps(
            {
                "status": "error",
                "error": str(e),
                "task": task,
            }
        )


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
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/tools/browser.py: %s", _bare_e
            )
        history.append(
            {
                "step": step_num,
                "output": str(output)[:500],
            }
        )
        if step_num >= max_steps:
            raise RuntimeError(f"Exceeded max_steps limit: {max_steps}")

    full_task = (
        f"{task_description}\n\n"
        "Safety constraints:\n"
        "1. Do not make purchases or enter credit card information\n"
        "2. Do not submit forms without explicit confirmation\n"
        "3. Do not create accounts or sign up for any service — ever\n"
        "4. If you see a login or sign-up page: STOP and report back — do not proceed\n"
        "5. Respect robots.txt and site terms of service\n"
        "6. Report extracted data back clearly when done"
    )

    initial_actions = None
    if url and url not in ("about:blank", ""):
        # browser_use 0.11.x renamed go_to_url → navigate
        initial_actions = [{"navigate": {"url": url}}]

    try:
        from browser_use.browser.session import BrowserSession

        _igor_session = BrowserSession(
            user_data_dir=_IGOR_PROFILE,
            profile_directory=os.getenv("IGOR_CHROME_PROFILE_DIR", "Profile 1"),
            channel="chrome",
            headless=False,
        )
        agent = Agent(
            task=full_task,
            llm=_make_llm(),
            browser_session=_igor_session,
            use_vision=True,
            max_actions_per_step=1,
            step_timeout=timeout,
            max_failures=3,
            initial_actions=initial_actions,
            register_new_step_callback=on_step,
        )

        logger.info(f"Browser task started: {task_description[:100]}...")
        result = await agent.run()

        # Extract final URL (final_state() removed in 0.12.x; use urls())
        try:
            visited = [u for u in (result.urls() or []) if u]
            if visited:
                final_url = visited[-1]
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/tools/browser.py: %s", _bare_e
            )

        # Extract result text — prefer final_result(), fall back to accumulated
        # extracted_content from all action steps (better than raw object repr)
        extracted = None
        try:
            extracted = result.final_result()
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/tools/browser.py: %s", _bare_e
            )
        if not extracted:
            try:
                parts = [
                    r.extracted_content
                    for r in result.all_results
                    if getattr(r, "extracted_content", None)
                ]
                extracted = (
                    "\n---\n".join(parts)
                    if parts
                    else "Task completed (no content extracted)"
                )
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


# ── Igor's own Chrome profile (authenticated as Igor) ────────────────────────
def _default_igor_profile() -> str:
    from ..paths import paths as _paths

    return str(_paths().instance / "accounts" / "chrome_igor_profile")


_IGOR_PROFILE = os.getenv("IGOR_CHROME_PROFILE_PATH") or _default_igor_profile()

# ── browse_as_employer — authenticated browsing via employer's Chrome profile ──

_EMPLOYER_PROFILE = os.getenv(
    "EMPLOYER_CHROME_PROFILE_PATH",
    str(Path.home() / ".config" / "google-chrome"),
)

# Trusted sources for browse_as_employer. Discord is explicitly excluded —
# requests there may not come from the employer directly.
_EMPLOYER_BROWSE_TRUSTED_SOURCES = frozenset({"repl", "stdin", "web", "cc_bridge"})


def browse_as_employer(
    task: str,
    url: str,
    max_steps: int = 20,
    timeout: int = 180,
    caller_source: str = "",
) -> str:
    """
    Browse the web using the employer's (Akien's) personal system Chrome profile
    (~/.config/google-chrome). Use when the employer directs Igor to access
    Akien's personal accounts or paywalled content via Akien's own browser session.

    NOT for Kindle/Amazon — use browser_use_task for that (Igor's own profile
    at chrome_igor_profile has the Amazon login set up for Igor's use).

    INHIBITION: Not available from Discord or untrusted channels. The employer's
    session carries real credentials — only use when the employer is present.

    caller_source: the message source (repl/web/discord/etc.) — used to enforce
    the channel trust gate. Igor should pass the active session source here.
    """
    # ── Channel trust gate ────────────────────────────────────────────────────
    # Inhibitory gate: employer's profile must not be used from public channels.
    # This is the coded form of the inhibitory habit — the habit fires first
    # in pondering; this is the backstop if execution is somehow reached.
    if caller_source and caller_source not in _EMPLOYER_BROWSE_TRUSTED_SOURCES:
        return json.dumps(
            {
                "status": "inhibited",
                "reason": (
                    f"browse_as_employer is not available from '{caller_source}'. "
                    "The employer's Chrome session carries real credentials and logged-in accounts. "
                    "It may only be used from trusted direct sessions (repl, web UI) where the "
                    "employer is present. Requests from Discord or other public channels could "
                    "come from anyone — the employer's accounts must not be exposed to that."
                ),
            }
        )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_as_employer(task, url=url, max_steps=max_steps, timeout=timeout)
            )
            return json.dumps(result, indent=2)
        finally:
            loop.close()
    except Exception as e:
        logger.exception("browse_as_employer failed")
        return json.dumps({"status": "error", "error": str(e), "task": task})


async def _run_as_employer(
    task_description: str,
    url: str,
    max_steps: int = 20,
    timeout: int = 180,
) -> dict:
    """
    Playwright persistent context pointing at the employer's Chrome profile.
    Uses the real Chrome binary so Kindle and other DRM-aware sites behave
    as if the employer is browsing normally.
    """
    from playwright.async_api import async_playwright

    steps_taken = 0
    final_url = url
    extracted_text = ""

    async with async_playwright() as p:
        # Remove stale Chrome singleton locks (left by unclean exits)
        import pathlib as _pathlib

        for _lock in ["SingletonLock", "SingletonSocket"]:
            _p = _pathlib.Path(_EMPLOYER_PROFILE) / _lock
            if _p.exists() or _p.is_symlink():
                _p.unlink(missing_ok=True)
        # launch_persistent_context reuses the profile's cookies, localStorage,
        # and session tokens — Igor inherits the employer's logged-in state.
        context = await p.chromium.launch_persistent_context(
            user_data_dir=_EMPLOYER_PROFILE,
            channel="chrome",  # real Chrome, not bundled Chromium
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = await context.new_page()

        try:
            logger.info(f"browse_as_employer: navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            final_url = page.url

            # Run the task as a series of steps guided by the task description.
            # For reading tasks this is: wait for content, extract text, paginate.
            for step in range(max_steps):
                steps_taken = step + 1
                await page.wait_for_timeout(1500)

                # Extract visible text content
                content = await page.evaluate("""() => {
                    // Remove scripts, styles, nav cruft
                    const noise = document.querySelectorAll(
                        'script,style,nav,header,footer,[role=navigation]'
                    );
                    noise.forEach(n => n.remove());
                    return document.body ? document.body.innerText.trim() : '';
                }""")

                if content and len(content) > 100:
                    extracted_text = content[:8000]
                    break

            logger.info(
                f"browse_as_employer: extracted {len(extracted_text)} chars in {steps_taken} steps"
            )
            return {
                "status": "success",
                "result": extracted_text or "(no readable content extracted)",
                "final_url": final_url,
                "steps_taken": steps_taken,
            }

        except Exception as e:
            logger.exception(f"browse_as_employer page error: {e}")
            return {
                "status": "error",
                "error": str(e),
                "final_url": final_url,
                "steps_taken": steps_taken,
            }
        finally:
            await context.close()


# ── read_kindle_chunk — deterministic Playwright-based Kindle page reader ────────

_CDP_PORT = int(os.getenv("IGOR_CHROME_CDP_PORT", "9222"))


def _cdp_ready(port: int = _CDP_PORT) -> bool:
    """Return True if Chrome is already listening for CDP connections."""
    import urllib.request

    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _launch_chrome_cdp(profile_path: str, port: int = _CDP_PORT) -> None:
    """
    Launch Chrome as a real OS subprocess with remote debugging enabled.
    Chrome runs in the user session and has full GNOME keyring access,
    so encrypted cookies (Amazon session etc.) are decrypted automatically.
    """
    import subprocess

    profile_dir = os.getenv("IGOR_CHROME_PROFILE_DIR", "Profile 1")
    cmd = [
        "google-chrome",
        f"--user-data-dir={profile_path}",
        f"--profile-directory={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    logger.info(f"read_kindle_chunk: launching Chrome CDP — {' '.join(cmd)}")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _read_kindle_chunk_impl(
    asin: str,
    start_page: int,
    pages_per_chunk: int,
    timeout: int,
) -> dict:
    """
    Playwright-based Kindle reader. No LLM agent — deterministic keyboard + DOM.
    Connects to Chrome via CDP (launched as real subprocess so GNOME keyring
    decrypts Amazon cookies automatically). Navigates to the Kindle reader for
    the given ASIN, jumps to start_page, reads pages_per_chunk pages via ArrowRight.
    Extracts text from .text-div elements on each page.
    """
    import asyncio
    from playwright.async_api import async_playwright

    extracted_pages = []
    last_page = start_page

    # Ensure Chrome is running with CDP — launch if not already up
    if not _cdp_ready(_CDP_PORT):
        _launch_chrome_cdp(_IGOR_PROFILE, _CDP_PORT)
        for _ in range(30):
            await asyncio.sleep(1)
            if _cdp_ready(_CDP_PORT):
                break
        else:
            raise RuntimeError(f"Chrome CDP not ready on port {_CDP_PORT} after 30s")
        await asyncio.sleep(1)  # brief extra settle after CDP reports ready

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{_CDP_PORT}")
        # Use the existing context (already has authenticated cookies)
        context = (
            browser.contexts[0] if browser.contexts else await browser.new_context()
        )
        page = await context.new_page()

        try:
            url = f"https://read.amazon.com/?asin={asin}"
            logger.info(f"read_kindle_chunk: loading {url} start_page={start_page}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for reader to render at least one text div
            await page.wait_for_selector(".text-div", timeout=30000)
            await page.wait_for_timeout(5000)  # let DRM/JS finish rendering

            # Click the reader area to give it keyboard focus
            try:
                reader = await page.query_selector(".text-div")
                if reader:
                    await reader.click()
                    await page.wait_for_timeout(500)
            except Exception as e:
                log_error(
                    kind="TOOL_FAIL", detail=f"reader focus click failed: {e}"
                )  # non-fatal

            # ── Navigate to start_page ─────────────────────────────────────────
            if start_page <= 1:
                await page.keyboard.press("Control+Home")
                await page.wait_for_timeout(1500)
            else:
                # Kindle shows "Page N of M" at bottom — clicking opens a go-to input.
                navigated = False
                for sel in [
                    "#page-indicator",
                    ".kr-progress-bar-position",
                    "[data-testid='page-number']",
                    ".pageReadingInfo",
                ]:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            await el.click()
                            await page.wait_for_timeout(600)
                            await page.keyboard.press("Control+a")
                            await page.keyboard.type(str(start_page))
                            await page.keyboard.press("Enter")
                            await page.wait_for_timeout(2000)
                            navigated = True
                            logger.info(
                                f"read_kindle_chunk: navigated to page {start_page} via {sel}"
                            )
                            break
                    except Exception:
                        continue
                if not navigated:
                    logger.warning(
                        f"read_kindle_chunk: could not navigate to page {start_page} "
                        "— starting from current position"
                    )

            # ── Read pages_per_chunk pages ─────────────────────────────────────
            for i in range(pages_per_chunk):
                divs = await page.query_selector_all(".text-div")
                page_text = ""
                for div in divs:
                    t = await div.inner_text()
                    if t.strip():
                        page_text += t + "\n"

                if page_text.strip():
                    extracted_pages.append(page_text.strip())
                    last_page = start_page + i
                    logger.info(
                        f"read_kindle_chunk: page {last_page} — {len(page_text)} chars extracted"
                    )
                else:
                    logger.warning(
                        f"read_kindle_chunk: page {start_page + i} — no text found in .text-div"
                    )

                await page.keyboard.press("ArrowRight")
                await page.wait_for_timeout(2500)

            return {
                "status": "success",
                "asin": asin,
                "start_page": start_page,
                "last_page": last_page,
                "pages_read": len(extracted_pages),
                "text": "\n\n--- PAGE BREAK ---\n\n".join(extracted_pages),
            }

        except Exception as e:
            logger.exception(f"read_kindle_chunk error at page {last_page}: {e}")
            return {
                "status": "error",
                "error": str(e),
                "asin": asin,
                "start_page": start_page,
                "last_page": last_page,
                "pages_read": len(extracted_pages),
                "text": "\n\n--- PAGE BREAK ---\n\n".join(extracted_pages),
            }
        finally:
            await page.close()
            # Leave Chrome running — next chunk call connects to same session


def read_kindle_chunk(
    asin: str,
    start_page: int = 1,
    pages_per_chunk: int = 10,
    timeout: int = 120,
) -> str:
    """
    Read a chunk of pages from a Kindle ebook using Igor's logged-in Chrome profile.
    Uses direct Playwright keyboard control (ArrowRight) — no AI agent needed.

    Text is extracted from .text-div elements on each page. Call repeatedly with
    start_page = last_page + 1 to read the full book in chunks.

    Returns JSON with: asin, start_page, last_page, pages_read, text (all pages joined).
    On partial failure still returns whatever was extracted before the error.

    Args:
        asin: Kindle ASIN (e.g. "B000SEHLE6" for Making Money)
        start_page: Page to start from (1-indexed)
        pages_per_chunk: Number of pages to read per call (default 10)
        timeout: Not used for page timing (each page waits 1.2s); reserved
    """
    _ensure_virtual_display()
    logger.info(
        f"read_kindle_chunk: asin={asin} start_page={start_page} pages_per_chunk={pages_per_chunk}"
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _read_kindle_chunk_impl(asin, start_page, pages_per_chunk, timeout)
            )
            return json.dumps(result, indent=2)
        finally:
            loop.close()
    except Exception as e:
        logger.exception("read_kindle_chunk failed")
        return json.dumps({"status": "error", "error": str(e), "asin": asin})


registry.register(
    Tool(
        name="read_kindle_chunk",
        description=(
            "Read pages from a Kindle ebook using Igor's logged-in Chrome profile. "
            "Deterministic: uses ArrowRight key to paginate, extracts text from .text-div elements. "
            "Call repeatedly with start_page = last_page + 1 to read the full book in chunks. "
            "Returns extracted text plus last_page so you know where to resume."
        ),
        parameters={
            "type": "object",
            "properties": {
                "asin": {
                    "type": "string",
                    "description": "Kindle ASIN, e.g. 'B000SEHLE6' for Making Money by Pratchett",
                },
                "start_page": {
                    "type": "integer",
                    "description": "Page number to start from (default 1)",
                },
                "pages_per_chunk": {
                    "type": "integer",
                    "description": "Number of pages to read per call (default 10)",
                },
            },
            "required": ["asin"],
        },
        fn=read_kindle_chunk,
    )
)


# ── check_claude_balance — scrape Anthropic billing for current credit balance ──

_ANTHROPIC_BILLING_URL = "https://console.anthropic.com/settings/billing"
_BALANCE_JSON_PATH = paths().cc_channel / "anthropic_balance.json"


def check_claude_balance(caller_source: str = "") -> str:
    """
    Scrape the Anthropic console billing page for the current credit balance.

    Navigates to https://console.anthropic.com/settings/billing using the
    employer's Chrome profile, extracts the balance dollar amount, and writes
    {"balance_usd": X.XX, "fetched_at": ISO-timestamp} to
    ~/.unseen_university/cc_channel/anthropic_balance.json.

    caller_source: session source — only trusted direct channels (repl, web)
    may use the employer's browser session. Discord is excluded.

    Returns balance as a string, e.g. "balance_usd: 42.50".
    """
    if caller_source and caller_source not in _EMPLOYER_BROWSE_TRUSTED_SOURCES:
        return json.dumps(
            {
                "status": "inhibited",
                "reason": (
                    f"check_claude_balance is not available from '{caller_source}'. "
                    "Uses the employer's Chrome session — only repl/web allowed."
                ),
            }
        )

    logger.info(
        f"check_claude_balance: starting — navigating to Anthropic billing caller_source={caller_source!r}"
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_scrape_anthropic_balance())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"check_claude_balance: unhandled exception — {e}")
        return json.dumps({"status": "error", "error": str(e)})


async def _scrape_anthropic_balance() -> str:
    """Async implementation: navigate to billing page and parse balance."""
    import re
    from datetime import datetime, timezone

    from playwright.async_api import async_playwright

    extracted_text = ""

    async with async_playwright() as p:
        # Remove stale Chrome singleton locks (left by unclean exits)
        import pathlib as _pathlib

        for _lock in ["SingletonLock", "SingletonSocket"]:
            _p = _pathlib.Path(_EMPLOYER_PROFILE) / _lock
            if _p.exists() or _p.is_symlink():
                _p.unlink(missing_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=_EMPLOYER_PROFILE,
            channel="chrome",
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = await context.new_page()
        try:
            await page.goto(
                _ANTHROPIC_BILLING_URL, wait_until="domcontentloaded", timeout=30000
            )
            # Wait for SPA content to render
            await page.wait_for_timeout(3000)

            content = await page.evaluate("""() => {
                const noise = document.querySelectorAll(
                    'script,style,nav,header,footer,[role=navigation]'
                );
                noise.forEach(n => n.remove());
                return document.body ? document.body.innerText.trim() : '';
            }""")
            extracted_text = content[:4000]
        except Exception as e:
            logger.error(f"check_claude_balance: page error — {e}")
            await context.close()
            return json.dumps({"status": "error", "error": str(e)})
        finally:
            await context.close()

    # Parse balance from page text
    balance_usd = None
    patterns = [
        r"[Cc]redit[s]?\s*(?:remaining|balance|available)?[:\s]*\$?([\d,]+\.?\d*)",
        r"[Bb]alance[:\s]*\$?([\d,]+\.?\d*)",
        r"\$([\d,]+\.\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, extracted_text)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                balance_usd = float(raw)
                break
            except ValueError:
                continue

    fetched_at = datetime.now(timezone.utc).isoformat()

    if balance_usd is not None:
        payload = {"balance_usd": balance_usd, "fetched_at": fetched_at}
        _BALANCE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BALANCE_JSON_PATH.write_text(json.dumps(payload, indent=2))
        logger.info(
            f"check_claude_balance: complete — balance_usd={balance_usd} written to {_BALANCE_JSON_PATH}"
        )
        return f"balance_usd: {balance_usd}"
    else:
        logger.warning(
            f"check_claude_balance: could not parse balance from page text (len={len(extracted_text)})"
        )
        payload = {
            "balance_usd": None,
            "fetched_at": fetched_at,
            "raw_text": extracted_text[:500],
        }
        _BALANCE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BALANCE_JSON_PATH.write_text(json.dumps(payload, indent=2))
        return json.dumps(
            {
                "status": "parse_failed",
                "message": "Could not parse balance from billing page",
                "raw_text": extracted_text[:500],
            }
        )


# ── Register tool ─────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="browse_as_employer",
        description=(
            "Browse the web using the employer's (Akien's) personal system Chrome profile. "
            "Use when Akien directs Igor to access Akien's own personal accounts or paywalled "
            "content via Akien's browser session. "
            "NOT for Kindle/Amazon — use browser_use_task for that (Igor's own chrome profile "
            "has the Amazon login). "
            "NOT available from Discord or public channels — only from direct sessions "
            "where the employer is present."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What to do (e.g. 'Extract the balance from the billing page')",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Max page interactions (default 20)",
                },
                "caller_source": {
                    "type": "string",
                    "description": "The session source (repl/web/discord). Required for trust gate.",
                },
            },
            "required": ["task", "url"],
        },
        fn=browse_as_employer,
    )
)


registry.register(
    Tool(
        name="browser_use_task",
        description=(
            "Execute a browser automation task using AI-driven control via Igor's own "
            "logged-in Chrome profile. Use for: reading Kindle ebooks (read.amazon.com), "
            "accessing Igor's logged-in accounts, navigating sites, extracting data, "
            "interacting with pages, using web services like Gemini. "
            "Returns extracted data or confirmation of completion."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Natural language description of the task. "
                        "E.g., 'Go to read.amazon.com and find Making Money by Pratchett, read the first chapter'"
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "Optional starting URL (e.g., 'https://read.amazon.com')",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum browser actions to attempt (default 30)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait (default 300)",
                },
            },
            "required": ["task"],
        },
        fn=browser_use_task,
    )
)

registry.register(
    Tool(
        name="check_claude_balance",
        description=(
            "Check the current Anthropic credit balance by scraping the console billing page. "
            "Navigates to https://console.anthropic.com/settings/billing using the employer's "
            "logged-in Chrome profile and extracts the credit balance. "
            "Writes {balance_usd, fetched_at} to ~/.unseen_university/cc_channel/anthropic_balance.json. "
            "Only available from trusted direct sessions (repl, web UI) — not from Discord."
        ),
        parameters={
            "type": "object",
            "properties": {
                "caller_source": {
                    "type": "string",
                    "description": "The session source (repl/web/discord). Required for trust gate.",
                },
            },
            "required": [],
        },
        fn=check_claude_balance,
    )
)
