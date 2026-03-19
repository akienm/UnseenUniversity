"""
env_sync.py — D119: DB-first boot configuration.

At boot:
  1. Hydrate os.environ from the system config graph (fills in missing vars)
  2. Compare .env mtime with last_env_read stored in instance's SWARM node
  3. If .env is newer: re-read it, push non-credential vars into the global
     config subtree, update last_env_read mtime, re-hydrate

Graph structure:
  SYSCFG_ROOT (FACTUAL) — system configuration root
    SYSCFG_models   (FACTUAL) — model names, inference routing
    SYSCFG_features (FACTUAL) — feature gates and tuning knobs
  SWARM_ROOT (FACTUAL) — live instance registry
    SWARM_{instance_id} (FACTUAL) — per-instance metadata
      metadata.last_env_read_mtime — float mtime of last .env sync

Bootstrap vars (IGOR_INSTANCE_ID, IGOR_DB_URL, IGOR_RUNTIME_ROOT) are
never stored in the graph — they are needed before the DB is reachable.

Credential vars (names containing KEY/TOKEN/PASSWORD/SECRET/AUTH/PAT)
are skipped — those remain in .env only and use CREDENTIAL_REF nodes.

Hydration priority (highest wins):
  OS env vars already set (e.g. system env vars on Windows)
  → DB instance-scoped vars (future — not yet wired)
  → DB global-scoped vars (fills everything missing)
"""

import os
from pathlib import Path
from typing import Optional

from .memory.models import Memory, MemoryType

# ── Well-known node IDs ────────────────────────────────────────────────────────

SYSCFG_ROOT_ID = "SYSCFG_ROOT"
SYSCFG_MODELS_ID = "SYSCFG_models"
SYSCFG_FEATURES_ID = "SYSCFG_features"
SWARM_ROOT_ID = "SWARM_ROOT"

# ── Vars never stored in graph ─────────────────────────────────────────────────

_BOOTSTRAP_VARS = {"IGOR_INSTANCE_ID", "IGOR_DB_URL", "IGOR_RUNTIME_ROOT"}

_CREDENTIAL_WORDS = {"KEY", "TOKEN", "PASSWORD", "SECRET", "AUTH", "PAT", "CERT"}


def _is_credential(key: str) -> bool:
    upper = key.upper()
    return any(w in upper for w in _CREDENTIAL_WORDS)


# ── Category assignment ────────────────────────────────────────────────────────

_MODEL_PREFIXES = (
    "OLLAMA_",
    "OPENROUTER_",
    "IGOR_NE_LOCAL",
    "IGOR_WINNOW_LOCAL",
    "IGOR_LOCAL_MODEL",
)


def _category_id(key: str) -> str:
    return (
        SYSCFG_MODELS_ID
        if any(key.startswith(p) for p in _MODEL_PREFIXES)
        else SYSCFG_FEATURES_ID
    )


def _var_node_id(key: str) -> str:
    return f"SYSCFG_{key}"


def _swarm_node_id(instance_id: str) -> str:
    return f"SWARM_{instance_id}"


# ── Graph node helpers ─────────────────────────────────────────────────────────


def _ensure_node(
    cortex,
    node_id: str,
    narrative: str,
    parent_id: Optional[str],
    metadata: dict = None,
) -> Memory:
    existing = cortex.get(node_id)
    if existing:
        return existing
    mem = Memory(
        id=node_id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        parent_id=parent_id,
        metadata=metadata or {},
        portable=True,
        source="env_sync",
        confidence=1.0,
    )
    cortex.store(mem)
    return cortex.get(node_id) or mem


def _ensure_root_nodes(cortex) -> None:
    _ensure_node(cortex, SYSCFG_ROOT_ID, "system configuration root", parent_id=None)
    _ensure_node(
        cortex,
        SYSCFG_MODELS_ID,
        "system config: model and routing settings",
        parent_id=SYSCFG_ROOT_ID,
    )
    _ensure_node(
        cortex,
        SYSCFG_FEATURES_ID,
        "system config: feature flags and tuning",
        parent_id=SYSCFG_ROOT_ID,
    )
    _ensure_node(
        cortex,
        SWARM_ROOT_ID,
        "swarm: registry of running Igor instances",
        parent_id=None,
    )


def _ensure_instance_node(cortex, instance_id: str) -> Memory:
    node_id = _swarm_node_id(instance_id)
    existing = cortex.get(node_id)
    if existing:
        return existing
    return _ensure_node(
        cortex,
        node_id,
        f"swarm: instance {instance_id}",
        parent_id=SWARM_ROOT_ID,
        metadata={"instance_id": instance_id, "last_env_read_mtime": 0.0},
    )


# ── .env file parsing ──────────────────────────────────────────────────────────


def _parse_env_file(env_path: Path) -> dict:
    vars_dict = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                vars_dict[key] = value
    except Exception:
        pass
    return vars_dict


# ── Push / hydrate ─────────────────────────────────────────────────────────────


def push_vars_to_graph(cortex, vars_dict: dict) -> int:
    """Upsert env vars into the system config graph. Returns count of vars pushed."""
    _ensure_root_nodes(cortex)
    pushed = 0
    for key, value in vars_dict.items():
        if key in _BOOTSTRAP_VARS or _is_credential(key):
            continue
        node_id = _var_node_id(key)
        cat_id = _category_id(key)
        mem = Memory(
            id=node_id,
            narrative=f"system config: {key}={value}",
            memory_type=MemoryType.FACTUAL,
            parent_id=cat_id,
            metadata={"env_key": key, "env_value": value, "scope": "global"},
            portable=True,
            source="env_sync",
            confidence=1.0,
        )
        cortex.store(mem)
        pushed += 1
    return pushed


def hydrate_from_graph(cortex) -> int:
    """Load system config from graph into os.environ. Only fills unset keys. Returns count."""
    filled = 0
    for cat_id in (SYSCFG_MODELS_ID, SYSCFG_FEATURES_ID):
        for mem in cortex.get_children(cat_id):
            key = mem.metadata.get("env_key")
            value = mem.metadata.get("env_value")
            if key and value is not None and key not in os.environ:
                os.environ[key] = str(value)
                filled += 1
    return filled


# ── Main entry point ───────────────────────────────────────────────────────────


def boot_env_sync(cortex, instance_id: str, env_path: Path) -> None:
    """
    D119 boot sequence:
      1. Hydrate os.environ from system config graph (fills missing vars)
      2. If .env mtime changed: re-read, push to graph, re-hydrate
    """
    # Step 1: hydrate from graph — works even on first boot (no-op if graph empty)
    hydrate_from_graph(cortex)

    # Step 2: check .env mtime
    try:
        env_mtime = env_path.stat().st_mtime if env_path.exists() else 0.0
    except Exception:
        return

    instance_node = _ensure_instance_node(cortex, instance_id)
    last_mtime = float(instance_node.metadata.get("last_env_read_mtime", 0.0))

    if env_mtime <= last_mtime:
        return  # No change — graph is already up to date

    # Step 3: .env changed (or never read) — push to graph
    vars_dict = _parse_env_file(env_path)
    pushed = push_vars_to_graph(cortex, vars_dict)

    # Update instance node mtime
    instance_node.metadata["last_env_read_mtime"] = env_mtime
    cortex.store(instance_node)

    # Re-hydrate with newly pushed values
    hydrate_from_graph(cortex)

    import logging

    logging.getLogger(__name__).info(
        f"[env_sync] synced {pushed} vars from .env → graph (instance={instance_id})"
    )
