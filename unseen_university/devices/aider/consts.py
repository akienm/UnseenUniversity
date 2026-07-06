"""AiderDevice instance constants.

One canonical place for the identity, mailbox, model ladder, and inference
endpoint. Everything is env-overridable so the same code runs on the main box,
the 4 swarm laptops, or a Windows exploration box (T-aider-swarm-deployment).
"""

from __future__ import annotations

import os
from pathlib import Path

DEVICE_ID = "aider"
INSTANCE_ABBREVIATION = "Aider"
MAILBOX = "aider.0"

# Akien 2026-07-06: ~2 instances per box is CPU-safe. Swarm deployment tunes the
# real per-box cap; the device gate stays conservative.
MAX_INSTANCES = int(os.environ.get("AIDER_MAX_INSTANCES", "2"))

# Model ladder. qwen3-coder:30b is MoE (~3B active) — proven ~2.2x faster than
# dense devstral and equally correct (project_aider_builder_viable). devstral is
# the fallback / swarm redundancy.
DEFAULT_MODEL = os.environ.get("AIDER_DEVICE_MODEL", "qwen3-coder:30b")
FALLBACK_MODEL = os.environ.get("AIDER_DEVICE_FALLBACK_MODEL", "devstral-small-2:24b")

# Hex ollama endpoint ($0 inference). Overridable for other boxes / Ollama Cloud.
# aider reads OLLAMA_API_BASE; the runner exports whatever this resolves to.
HEX_OLLAMA = (
    os.environ.get("HEX_OLLAMA")
    or os.environ.get("OLLAMA_API_BASE")
    or "http://10.0.0.100:11434"
)


def aider_bin() -> Path:
    """Resolve the aider executable. Env AIDER_BIN wins; else the conventional
    venv (POSIX bin/ or Windows Scripts/); resolution is deferred to call time so
    a Windows box picks its own layout."""
    override = os.environ.get("AIDER_BIN")
    if override:
        return Path(override)
    venv = Path(os.environ.get("AIDER_VENV", str(Path.home() / ".aider-venv")))
    posix = venv / "bin" / "aider"
    if posix.exists():
        return posix
    windows = venv / "Scripts" / "aider.exe"
    if windows.exists():
        return windows
    return posix  # POSIX default; self_test surfaces the miss with a clear message
