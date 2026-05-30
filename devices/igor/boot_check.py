import logging

"""
boot_check.py — Verify required Ollama models on cluster machines at boot.

All cluster machines run Ollama. Primary local inference backend for preparse
and tier.2 reasoning.

Required Ollama models (checked and auto-pulled at boot):
  nomic-embed-text  — universal embedding model; must be identical across cluster
  llama3.2:1b       — local preparse + tier.2 reasoning (override: OLLAMA_LOCAL_MODEL)

Runs in a daemon thread at startup so Igor is not blocked.
Logs results to ~/.unseen_university/claudecode/changes.log (CSB format, newest first)
and writes a summary to ring memory for NE integration.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from .paths import paths
from .cognition.forensic_logger import log_error

MACHINES_JSON = paths().machines_json
CHANGES_LOG = paths().claudecode / "changes.log"
OLLAMA_PORT = 11434
REQUIRED_MODELS = [
    "nomic-embed-text",  # embeddings
    os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b"),  # preparse + tier.2
]
# Batch model only pulled on priority.batch / priority.background machines
BATCH_MODELS = [
    os.getenv("OLLAMA_BATCH_MODEL", "qwen2.5:14b"),  # large reasoning
]
_BATCH_PRIORITIES = {"priority.batch", "priority.background"}
CHECK_TIMEOUT = 5  # seconds per reachability probe
PULL_TIMEOUT = 600  # seconds — model pull can take a while on first run


# ── Internal helpers ───────────────────────────────────────────────────────────


def _parse_online_machines() -> list[dict]:
    """Parse machines.json; return dicts for machines with non-null/non-offline IPs."""
    if not MACHINES_JSON.exists():
        return []
    try:
        data = json.loads(MACHINES_JSON.read_text(encoding="utf-8"))
        machines = []
        for m in data.get("machines", []):
            ip = m.get("ip") or ""
            if ip and m.get("status", "online") != "offline":
                machines.append(m)
        return machines
    except Exception:
        return []


def _get_available_models(ip: str) -> Optional[list[str]]:
    """
    Query GET /api/tags on the machine at `ip`.
    Returns list of model name roots (before ':tag'), or None if unreachable.
    """
    url = f"http://{ip}:{OLLAMA_PORT}/api/tags"
    try:
        with urlopen(url, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        models = data.get("models", [])
        # Normalise: strip :tag suffix so "nomic-embed-text:latest" matches "nomic-embed-text"
        return [m["name"].split(":")[0].lower() for m in models]
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return None


def _pull_model(ip: str, model: str) -> bool:
    """
    POST /api/pull on machine at `ip` with stream=false.
    Returns True on success, False on any error.
    """
    url = f"http://{ip}:{OLLAMA_PORT}/api/pull"
    body = json.dumps({"name": model, "stream": False}).encode()
    req = Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urlopen(req, timeout=PULL_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data.get("status") == "success"
    except (URLError, OSError, json.JSONDecodeError):
        return False


def _prepend_log(entry: str):
    """Prepend one CSB line to changes.log (newest-first format)."""
    CHANGES_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = (
            CHANGES_LOG.read_text(encoding="utf-8") if CHANGES_LOG.exists() else ""
        )
        CHANGES_LOG.write_text(entry + "\n" + existing, encoding="utf-8")
    except OSError as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in devices/igor/boot_check.py: %s", _bare_e
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def run(cortex=None):
    """
    Check all online machines for required Ollama models.
    Pull any that are missing. Log results to changes.log and ring memory.
    """
    machines = _parse_online_machines()
    if not machines:
        return

    ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
    results: list[str] = []

    for machine in machines:
        hostname = machine.get("hostname", "unknown")
        ip = machine.get("ip", "")
        priority = machine.get("priority", "unknown")

        # ── Ollama model checks ────────────────────────────────────────────
        available = _get_available_models(ip)
        if available is None:
            results.append(
                f"BOOT_CHECK|{ts}|{hostname}|{ip}|{priority}|ollama_unreachable"
            )
            continue

        models_to_check = list(REQUIRED_MODELS)
        if priority in _BATCH_PRIORITIES:
            models_to_check.extend(BATCH_MODELS)

        for model in models_to_check:
            model_root = model.split(":")[0].lower()
            if model_root in available:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|present")
            else:
                results.append(
                    f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|missing|pulling"
                )
                success = _pull_model(ip, model)
                status = "pulled_ok" if success else "pull_failed"
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|{status}")

    # Write each result line to changes.log (newest-first: prepend in reverse)
    for entry in reversed(results):
        _prepend_log(entry)

    # Push concise summary to ring memory so NE can integrate it
    if cortex is not None:
        ok_count = sum(
            1 for r in results if r.endswith("|present") or r.endswith("|pulled_ok")
        )
        fail_count = sum(
            1
            for r in results
            if r.endswith("|pull_failed") or r.endswith("|unreachable")
        )
        summary = (
            f"BOOT_CHECK_DONE|{ts}|machines={len(machines)}"
            f"|required={','.join(REQUIRED_MODELS)}"
            f"|ok={ok_count}|fail={fail_count}"
        )
        try:
            cortex.write_ring(summary, category="system_info")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/boot_check.py: %s", _bare_e
            )


def start(cortex=None):
    """
    Start model boot check in a daemon thread. Returns immediately.
    Igor startup is not blocked; results appear in ring memory and changes.log.
    """
    t = threading.Thread(target=run, args=(cortex,), daemon=True, name="boot-check")
    t.start()
    try:
        from .cognition.daemon_supervisor import supervisor as _sup

        _sup.register("boot-check", t, one_shot=True)
    except Exception as e:
        log_error(
            kind="TOOL_FAIL", detail=f"daemon supervisor registration failed: {e}"
        )
