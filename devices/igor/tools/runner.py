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

import subprocess
import sys
from pathlib import Path

from .registry import Tool, registry

WORKSPACE = Path(__file__).parent.parent.parent / "workspace"
WORKSPACE.mkdir(exist_ok=True)

DEFAULT_TIMEOUT = 30  # seconds


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

registry.register(Tool(
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
))

registry.register(Tool(
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
))
