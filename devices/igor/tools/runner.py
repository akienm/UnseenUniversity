import logging

"""
Runner tools - execute bash commands and Python code snippets.

Safety model:
  - Bash runs in a subprocess with a timeout (default 30s).
  - Python runs via `python3 -c` so it has full interpreter access,
    NOT sandboxed — treat it as running as the current OS user.
  - Both capture stdout + stderr and return them as a string.
  - Working directory is set to workspace/ so relative paths land there.
  - There is NO container/VM isolation. Akien has deliberately granted
    this capability. Use responsibly.

Why this exists:
  - Enables Igor to run experiments, build tools, explore data, and
    perform system tasks without Akien having to copy-paste code.
  - FAIL = Further Advance In Learning: being able to run code and
    observe real output dramatically improves the feedback loop.
"""

import os
import subprocess
import sys
from pathlib import Path

from devices.igor.tools.registry import Tool, registry

WORKSPACE = Path(__file__).parent.parent.parent / "workspace"
WORKSPACE.mkdir(exist_ok=True)

DEFAULT_TIMEOUT = 30  # seconds


import time
from datetime import datetime
from ..cognition.reasoners.ollama_reasoner import (
    OllamaReasoner,
    OLLAMA_LOCAL_MODEL,
    OLLAMA_HOST,
)


def _run(args: list[str], timeout: int, input_text: str = "") -> str:
    """
    Execute a subprocess and return combined stdout/stderr.
    """
    try:
        result = subprocess.run(
            args,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text or None,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        if result.returncode != 0:
            parts.append(f"[exit code: {result.returncode}]")

        # Track exit code 127 (command not found) as misfire
        if result.returncode == 127 and args:
            from .misfire_counter import get_misfire_counter

            command_str = " ".join(args) if isinstance(args, list) else str(args)
            counter = get_misfire_counter()
            counter.record_bash_exit(command_str, result.returncode)

        return "\n".join(parts).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] Process exceeded {timeout}s limit and was killed."
    except FileNotFoundError as e:
        return f"[ERROR] Command not found: {e}"
    except Exception as e:
        return f"[ERROR] {e}"


# ── Bash runner ────────────────────────────────────────────────────────────────


def run_bash(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Run a bash command string. Working directory is workspace/.
    Captures stdout and stderr. Returns combined output.
    """
    return _run(["bash", "-c", command], timeout=timeout)


# ── Python runner ──────────────────────────────────────────────────────────────


def run_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Execute a Python code snippet using the current interpreter.
    Working directory is workspace/. Captures stdout and stderr.

    Note: this is NOT sandboxed — it runs with full OS permissions.
    """
    return _run([sys.executable, "-c", code], timeout=timeout)


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_bash",
        description=(
            "Run a bash command in Igor's workspace directory. "
            "Captures and returns stdout + stderr. "
            "Has a configurable timeout (default 30s). "
            "NOT sandboxed — runs as the current OS user. Use responsibly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command string to execute (e.g. 'ls -la' or 'pip show numpy')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait before killing the process (default 30, max suggested 120)",
                },
            },
            "required": ["command"],
        },
        fn=run_bash,
    )
)

# Alias: 'bash' routes to run_bash
registry.register(
    Tool(
        name="bash",
        description=(
            "Alias for run_bash. Run a bash command in Igor's workspace directory. "
            "Captures and returns stdout + stderr. "
            "Has a configurable timeout (default 30s). "
            "NOT sandboxed — runs as the current OS user. Use responsibly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command string to execute (e.g. 'ls -la' or 'pip show numpy')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait before killing the process (default 30, max suggested 120)",
                },
            },
            "required": ["command"],
        },
        fn=run_bash,
    )
)


registry.register(
    Tool(
        name="run_python",
        description=(
            "Execute a Python code snippet using Igor's interpreter. "
            "Working directory is workspace/. Captures stdout + stderr. "
            "NOT sandboxed — full OS access. "
            "Good for experiments, calculations, data wrangling, and building things."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait before killing the process (default 30)",
                },
            },
            "required": ["code"],
        },
        fn=run_python,
    )
)


def get_current_time() -> str:
    """Return the current local date and time."""
    return datetime.now().strftime("%A, %Y-%m-%d  %H:%M:%S")


registry.register(
    Tool(
        name="get_current_time",
        description="Return the current local date and time.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=get_current_time,
    )
)


def restart_self(note: str = "") -> str:
    """
    Signal Igor to restart cleanly on the next main-loop iteration.

    Writes ~/.unseen_university/igor_<instance_id>/restart.flag — the main loop
    checks this flag at the top of each idle cycle and exits with code 42,
    which the bash wrapper catches and relaunches.

    Optional note is written to ring memory so Igor can read it on wakeup.
    Equivalent to the /restart command but callable as a tool from any channel
    (web UI, Discord, API — not just stdin).
    """
    from ..paths import paths as _paths

    flag_path = _paths().instance / "restart.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(note or "restart requested via tool")
    return (
        f"Restart flag written. I will restart cleanly on the next loop cycle. "
        + (f"Note for wakeup: {note}" if note else "")
    ).strip()


def check_process(name: str) -> str:
    """
    Check whether a process matching `name` is currently running.
    Uses pgrep to search by process name pattern.
    Returns a structured string: running status, PID list, and count.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-af", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if lines:
            pids = [l.split()[0] for l in lines]
            return (
                f"RUNNING|name={name}|count={len(pids)}|pids={','.join(pids)}"
                f"|processes={'; '.join(lines[:5])}"
            )
        return f"NOT_RUNNING|name={name}"
    except FileNotFoundError:
        # pgrep not available — fall back to ps
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        matches = [l for l in result.stdout.splitlines() if name.lower() in l.lower()]
        if matches:
            return f"RUNNING|name={name}|count={len(matches)}|via=ps_aux"
        return f"NOT_RUNNING|name={name}|via=ps_aux"
    except Exception as e:
        return f"ERROR|name={name}|{e}"


registry.register(
    Tool(
        name="check_process",
        description=(
            "Check whether a named process is currently running on this machine. "
            "Uses pgrep to search by process name pattern. "
            "Returns running status, PID list, and matching process lines. "
            "Useful for: verifying Igor is up, checking if Ollama is running, "
            "confirming background jobs are alive."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Process name or pattern to search for (e.g. 'igor', 'ollama', 'python')",
                },
            },
            "required": ["name"],
        },
        fn=check_process,
    )
)


# ── Project-aware git / ticket tools (D095) ────────────────────────────────


def _resolve_repo_path(repo_path: str = None, project_id: str = None) -> str:
    """
    Resolve a repository path.
    Priority: explicit repo_path > project_id lookup in DB > ~/TheIgors fallback.
    """
    if repo_path:
        return repo_path
    if project_id:
        try:
            from ..memory.cortex import Cortex as _Cortex

            _cortex = _Cortex(None)
            _mem = _cortex.get(project_id)
            if _mem and _mem.metadata and _mem.metadata.get("path"):
                return _mem.metadata["path"]
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/tools/runner.py: %s", _bare_e
            )
    return str(Path.home() / "TheIgors")


def git_log(repo_path: str = None, project_id: str = None, n: int = 20) -> str:
    """
    Return the last N git log entries for a project repository.
    If project_id is given, the path is looked up from Igor's DB.
    Defaults to ~/TheIgors if neither is provided.
    """
    path = _resolve_repo_path(repo_path, project_id)
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{int(n)}"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = result.stdout.strip()
        if result.returncode != 0:
            return f"[git log error] {result.stderr.strip() or 'exit ' + str(result.returncode)}"
        return out or "(no commits)"
    except FileNotFoundError:
        return "[ERROR] git not found in PATH"
    except Exception as e:
        return f"[ERROR] {e}"


def find_tickets(
    repo_path: str = None,
    project_id: str = None,
    query: str = "",
    state: str = "open",
) -> str:
    """
    List GitHub issues for a project repository using gh CLI.
    state: open | closed | all
    query: optional search string (gh issue list --search)
    """
    path = _resolve_repo_path(repo_path, project_id)
    cmd = ["gh", "issue", "list", "--state", state, "--limit", "30"]
    if query:
        cmd += ["--search", query]
    try:
        result = subprocess.run(
            cmd,
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout.strip()
        if result.returncode != 0:
            return f"[gh error] {result.stderr.strip() or 'exit ' + str(result.returncode)}"
        return out or "(no matching issues)"
    except FileNotFoundError:
        return "[ERROR] gh CLI not found in PATH"
    except Exception as e:
        return f"[ERROR] {e}"


def list_projects() -> str:
    """
    List all projects registered in Igor's lists.projects table.
    Returns a formatted summary of each project (id, path, github_repo).
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        _cortex = _Cortex(None)
        items = _cortex.list_all("lists.projects")
        if not items:
            return "(no projects registered)"
        lines = []
        for item in items:
            mem_id = item.get("ref_id") or item["item_key"]
            details = item.get("item_value") or ""
            lines.append(f"  {item['item_key']:24s}  ref={mem_id}  {details}")
        return f"projects ({len(items)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


registry.register(
    Tool(
        name="git_log",
        description=(
            "Return the last N git log entries for a project repository. "
            "Pass project_id to look up the path from Igor's memory, "
            "or repo_path to specify directly. Defaults to ~/TheIgors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Absolute path to git repo",
                },
                "project_id": {
                    "type": "string",
                    "description": "Igor memory ID of the project (e.g. 'Project:TheIgors')",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of commits to show (default 20)",
                },
            },
            "required": [],
        },
        fn=git_log,
    )
)

registry.register(
    Tool(
        name="find_tickets",
        description=(
            "List GitHub issues for a project using gh CLI. "
            "Pass project_id to resolve the repo path from Igor's memory. "
            "state: open | closed | all. query: optional search string."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Absolute path to git repo",
                },
                "project_id": {
                    "type": "string",
                    "description": "Igor memory ID of the project",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search string (gh --search)",
                },
                "state": {
                    "type": "string",
                    "description": "Issue state: open | closed | all (default open)",
                },
            },
            "required": [],
        },
        fn=find_tickets,
    )
)

registry.register(
    Tool(
        name="list_projects",
        description="List all projects registered in Igor's lists.projects table.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=list_projects,
    )
)


registry.register(
    Tool(
        name="restart_self",
        description=(
            "Restart Igor cleanly. Writes the restart flag; the main loop picks it up "
            "on the next idle cycle and exits with code 42 (bash wrapper relaunches). "
            "Use when asked to restart, or after self-edits that need to take effect. "
            "Optionally pass a note that will be readable after wakeup."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "Optional message to self — readable after restart via ring memory",
                },
            },
            "required": [],
        },
        fn=restart_self,
    )
)


def exit_self(note: str = "") -> str:
    """
    Signal Igor to exit cleanly (no restart) on the next main-loop iteration.

    Writes ~/.unseen_university/igor_<instance_id>/exit.flag — the main loop exits with
    code 0, which the bash/PS wrapper does NOT restart (only exit code 42 triggers
    a restart).

    Use for 'stop igor', 'exit igor', 'shutdown' — anything that should halt
    this instance permanently until manually restarted.
    """
    from ..paths import paths as _paths

    flag_path = _paths().instance / "exit.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(note or "exit requested via tool")
    return "Exit flag written. I will shut down cleanly on the next loop cycle."


registry.register(
    Tool(
        name="exit_self",
        description=(
            "Shut down Igor cleanly (no restart). Writes the exit flag; the main loop "
            "exits with code 0 so the wrapper does not relaunch. "
            "Use for 'stop igor', 'exit igor', 'shutdown', 'please exit'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "Optional reason for the shutdown",
                },
            },
            "required": [],
        },
        fn=exit_self,
    )
)


def cluster_status(**_kwargs) -> str:
    """Return current cluster router state — which machines are up, their load scores."""
    try:
        from ..cognition.cluster_router import router as _router

        _router.force_refresh()
        lines = _router.status_lines()
        return "Cluster inference machines:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Cluster router unavailable: {exc}"


def set_inference_override(machine: str = "", **_kwargs) -> str:
    """
    Pin all local inference routing to a specific machine name (e.g. 'local', 'reasoning').
    Pass machine="" or machine="auto" to clear the override and resume dynamic routing.
    """
    try:
        from ..cognition.cluster_router import router as _router

        if not machine or machine.lower() == "auto":
            _router.clear_override()
            return "Inference override cleared — dynamic routing resumed."
        _router.set_override(machine)
        return f"Inference override set → '{machine}'. All local calls routed there until cleared."
    except Exception as exc:
        return f"Override failed: {exc}"


registry.register(
    Tool(
        name="cluster_status",
        description=(
            "Show the current state of the inference cluster: which Ollama machines "
            "are healthy, their load scores, active models, and response times. "
            "Use when asked about machine load or inference routing."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=cluster_status,
    )
)

registry.register(
    Tool(
        name="set_inference_override",
        description=(
            "Pin local inference to a specific machine ('local' or 'reasoning'). "
            "Pass machine='' or machine='auto' to resume dynamic routing. "
            "Use when Akien says 'use yoga9i for everything' or 'route locally only'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Machine name: 'local', 'reasoning', or '' for auto",
                },
            },
            "required": [],
        },
        fn=set_inference_override,
    )
)
