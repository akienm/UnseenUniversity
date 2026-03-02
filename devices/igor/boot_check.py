"""
boot_check.py — Verify KoboldCpp health + required Ollama models on cluster machines.

All cluster machines run KoboldCpp (port 5001) with Llama-3.2-1B-Instruct-Q4_K_M-GGUF.
KoboldCpp is the primary local inference backend for NE and preparse.
Ollama is retained for embeddings only (nomic-embed-text).

Required Ollama models:
  nomic-embed-text  — universal embedding model; must be identical across cluster

KoboldCpp health checked via GET /api/v1/info on koboldcpp_port
(read from machines.csv; defaults to 5001 if column present but empty).

Runs in a daemon thread at startup so Igor is not blocked.
Logs results to ~/.TheIgors/claudecode/changes.log (CSB format, newest first)
and writes a summary to ring memory for NE integration.
"""

import csv
import io
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

MACHINES_CSV       = Path.home() / ".TheIgors" / "local" / "machines.csv"
CHANGES_LOG        = Path.home() / ".TheIgors" / "claudecode" / "changes.log"
OLLAMA_PORT        = 11434
KOBOLDCPP_PORT_DEFAULT = 5001
REQUIRED_MODELS    = ["nomic-embed-text"]   # embeddings only; KoboldCpp handles reasoning
CHECK_TIMEOUT      = 5    # seconds per reachability probe
PULL_TIMEOUT       = 600  # seconds — model pull can take a while on first run


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_online_machines() -> list[dict]:
    """Parse machines.csv; return dicts for machines with non-OFFLINE IPs."""
    if not MACHINES_CSV.exists():
        return []
    try:
        text = MACHINES_CSV.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        machines = []
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            ip = row.get("IP", "").strip()
            if ip and ip.upper() != "OFFLINE":
                machines.append(row)
        return machines
    except Exception:
        return []


def _check_koboldcpp(ip: str, port: int = KOBOLDCPP_PORT_DEFAULT) -> Optional[bool]:
    """
    Probe KoboldCpp health endpoint GET /api/v1/info on the given machine.
    Returns True if healthy, False if reachable but unhealthy, None if unreachable.
    """
    url = f"http://{ip}:{port}/api/v1/info"
    try:
        with urlopen(url, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return bool(data)
    except (URLError, OSError):
        return None
    except (json.JSONDecodeError, ValueError):
        return False


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
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
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
        existing = CHANGES_LOG.read_text(encoding="utf-8") if CHANGES_LOG.exists() else ""
        CHANGES_LOG.write_text(entry + "\n" + existing, encoding="utf-8")
    except OSError:
        pass


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
        hostname = machine.get("Hostname", "unknown")
        ip       = machine.get("IP", "")
        priority = machine.get("Priority", "unknown")

        # ── KoboldCpp health check (Change 1) ─────────────────────────────
        kcc_port_str = machine.get("koboldcpp_port", "").strip()
        if kcc_port_str:
            try:
                kcc_port = int(kcc_port_str)
            except ValueError:
                kcc_port = KOBOLDCPP_PORT_DEFAULT
            kcc_status = _check_koboldcpp(ip, kcc_port)
            if kcc_status is True:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|koboldcpp:{kcc_port}|healthy")
            elif kcc_status is False:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|koboldcpp:{kcc_port}|unhealthy")
            else:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|koboldcpp:{kcc_port}|unreachable")

        # ── Ollama model checks (existing; retained as fallback) ───────────
        available = _get_available_models(ip)
        if available is None:
            results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{priority}|ollama_unreachable")
            continue

        for model in REQUIRED_MODELS:
            model_root = model.split(":")[0].lower()
            if model_root in available:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|present")
            else:
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|missing|pulling")
                success = _pull_model(ip, model)
                status = "pulled_ok" if success else "pull_failed"
                results.append(f"BOOT_CHECK|{ts}|{hostname}|{ip}|{model}|{status}")

    # Write each result line to changes.log (newest-first: prepend in reverse)
    for entry in reversed(results):
        _prepend_log(entry)

    # Push concise summary to ring memory so NE can integrate it
    if cortex is not None:
        ok_count    = sum(1 for r in results if r.endswith("|present") or r.endswith("|pulled_ok"))
        fail_count  = sum(1 for r in results if r.endswith("|pull_failed") or r.endswith("|unreachable"))
        summary = (
            f"BOOT_CHECK_DONE|{ts}|machines={len(machines)}"
            f"|required={','.join(REQUIRED_MODELS)}"
            f"|ok={ok_count}|fail={fail_count}"
        )
        try:
            cortex.write_ring(summary, category="system_info")
        except Exception:
            pass  # Never crash startup


def start(cortex=None):
    """
    Start model boot check in a daemon thread. Returns immediately.
    Igor startup is not blocked; results appear in ring memory and changes.log.
    """
    t = threading.Thread(target=run, args=(cortex,), daemon=True, name="boot-check")
    t.start()
