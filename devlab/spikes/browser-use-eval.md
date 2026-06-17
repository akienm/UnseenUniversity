# browser-use evaluation spike
**Date:** 2026-04-27  
**Ticket:** T-adc-browser-use-eval-spike  
**Verdict:** GO — with one shim caveat (see Q2)

---

## Q1: Interface stability — shimable?

**Answer: Yes, stable enough to shim against.**

Installed: `browser-use 0.12.2` (latest available at eval time: 0.12.6).

The library has been in active development (v0.1 → v0.12 over ~6 months) but the
core `Agent` + `BrowserSession` surface has been stable since v0.7. The entry
points haven't had breaking renames in the last 6 releases (0.11.x → 0.12.x).

Key API surface (v0.12):
```python
from browser_use.agent.service import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile

session = BrowserSession(cdp_url="http://localhost:9222")  # or headless default
agent = Agent(task="...", llm=llm, browser_session=session)
result = await agent.run(max_steps=10)
```

`Agent.__init__` has ~50 kwargs but only `task`, `llm`, and optionally `browser_session`
are required for basic use. The rest have stable defaults. This is the shimable surface —
the shim only needs to expose task dispatch and result retrieval.

**Breaking change risk:** Medium. The library is pre-1.0, and minor bumps (0.11→0.12)
have occasionally renamed internal classes (e.g. `BrowserConfig` → `BrowserSessionConfig`
was removed; `BrowserProfile` became the session persistence carrier). The shim should
import from the specific submodule paths (`browser_use.agent.service`, `browser_use.browser.session`)
rather than from `browser_use` top-level, which is lazy-loaded and less stable.

**Recommendation:** Pin to `browser-use>=0.12,<0.13` in the shim's requirements.
Re-eval on each minor bump before upgrading.

---

## Q2: Own shim or ride an existing automation shim?

**Answer: Needs its own thin shim, but with a shared subprocess pattern.**

The `Agent` is async-native and LLM-coupled — it's not a subprocess that you `start()`
and `stop()` like Postgres or Dovecot. This means it cannot ride the `PostgresShim`
or a generic "subprocess automation" pattern directly.

However, the **lifecycle operations** (launch browser, execute task, close browser) do
map to the BaseShim contract if we frame them correctly:

| BaseShim method | BrowserUseShim equivalent |
|---|---|
| `start()` | Launch a persistent Playwright browser (CDP on port 9222) |
| `stop()` | Close the browser process |
| `restart()` | stop() + start() |
| `self_test()` | Navigate to `about:blank`, confirm page title, close |
| `rollback()` | Kill browser PID + remove any profile lock files |

The `BrowserDevice` (separate from the shim) would expose `run_task(task: str) -> str`
as its primary operation — calling `Agent.run()` against the already-started browser.

The shim manages the browser process lifetime; the device manages task dispatch.
This is the right split: shim = infrastructure, device = behavior.

**Concrete shim shape:**
```python
class BrowserUseShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "browser-use"

    def start(self) -> bool:
        # subprocess.Popen(['google-chrome', '--remote-debugging-port=9222', '--headless=new'])
        # wait for port 9222 to accept connections (up to 10s)
        ...

    def stop(self) -> bool:
        # kill the chrome subprocess
        ...

    def self_test(self) -> dict:
        # asyncio.run(short_agent_task(task="Navigate to about:blank and return the page title"))
        ...
```

---

## Q3: What does "restart" mean for a browser session?

**Answer: Kill-and-reopen (not restore-state). Session state is ephemeral by default.**

Three possible interpretations evaluated:

**3a. Kill and reopen (recommended)**  
Terminate the Chrome process and relaunch. Playwright state is lost. This is the right
default: browser sessions accumulate cookies, local storage, and tab history that can
cause agents to behave differently after long uptime. A clean restart is reproducible.
`BrowserProfile.storage_state` can be used to persist auth cookies if needed (opt-in).

**3b. Restore session state**  
`BrowserProfile(storage_state="/path/to/state.json")` persists cookies and local storage
across restarts. Useful for authenticated workflows (e.g. Discord, Gmail). However this
introduces state coupling between restarts — a bad cookie set can cause repeated failures.
The shim should support this as an opt-in via config, not the default.

**3c. Reconnect to existing session**  
`BrowserSession(cdp_url="http://localhost:9222")` can reconnect to a running Chrome
without restarting it. Useful when you want persistent tabs (e.g. the web server device
staying connected to a live site). However if the Chrome process crashes, this pattern
leaves no recovery path. Reserve for the web-server device, not the general shim.

**Conclusion:** Default restart = kill + reopen (3a). Support 3b via `storage_state`
config. 3c is out of scope for the shim.

---

## Minimal connect/navigate/close spike result

Spike code (not committed — eval only):
```python
import asyncio
from browser_use.agent.service import Agent
from browser_use.browser.session import BrowserSession

async def _spike():
    session = BrowserSession()  # headless, default Playwright launch
    # Just connect + navigate, no LLM task
    await session.start()
    page = await session.get_current_page()
    await page.goto("about:blank")
    title = await page.title()
    await session.stop()
    return title

result = asyncio.run(_spike())
# => ''  (about:blank has no title — expected)
```

Result: `''` (about:blank returns empty title — correct). The session start/stop
lifecycle works. The `BrowserSession.start()` / `BrowserSession.stop()` pattern is the
correct lifecycle hook for the shim.

**Chrome binary:** `/usr/bin/google-chrome` present. Playwright 1.58.0 installed.
Headless launch confirmed working.

---

## Recommendation

**GO.** Proceed with:
- `T-adc-browser-use-device`: `BrowserDevice(BaseDevice)` — exposes `run_task(task, max_steps=20)`
- `T-adc-browser-use-shim`: `BrowserUseShim(BaseShim)` — manages Chrome subprocess on port 9222

Pin `browser-use>=0.12,<0.13`. Re-eval before each minor version upgrade.
Import from submodule paths, not top-level.
Default restart = kill+reopen. Storage state persistence is opt-in via `BrowserProfile`.
