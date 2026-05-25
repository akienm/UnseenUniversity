import logging

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

# T-safety-gates-above-env-sync (Pass-2 Area 4 P1-8.1):
# These flags form Igor's primary safety perimeter. They MUST come from
# the file system, read once at boot, and never round-trip through the
# config graph. Otherwise any engram with cortex.store() access to a
# SYSCFG_* node can flip them (IGOR_TIER5_ENABLED gates direct Anthropic;
# IGOR_ARBITER_ENABLED gates the human-approval queue;
# IGOR_SELF_EDIT_ENABLED gates all source writes). Graph-poisoning
# scenario was CONFIRMED_WORSE in Pass 2 — the fix is cheap:
# exclude these names from push AND hydrate.
SAFETY_GATE_NAMES = frozenset(
    {
        "IGOR_TIER5_ENABLED",
        "IGOR_ARBITER_ENABLED",
        "IGOR_SELF_EDIT_ENABLED",
    }
)


def _is_safety_gate(key: str) -> bool:
    return key in SAFETY_GATE_NAMES


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
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in devices/igor/env_sync.py: %s", _bare_e
        )
    return vars_dict


# ── Push / hydrate ─────────────────────────────────────────────────────────────


def push_vars_to_graph(cortex, vars_dict: dict) -> int:
    """Upsert env vars into the system config graph. Returns count of vars pushed.

    T-safety-gates-above-env-sync: SAFETY_GATE_NAMES are excluded from the
    graph round-trip — file system is their only source of truth.
    """
    _ensure_root_nodes(cortex)
    pushed = 0
    for key, value in vars_dict.items():
        if key in _BOOTSTRAP_VARS or _is_credential(key) or _is_safety_gate(key):
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
    """Load system config from graph into os.environ. Only fills unset keys. Returns count.

    T-safety-gates-above-env-sync: never rehydrate SAFETY_GATE_NAMES — they
    must come from the file system only. A graph node with these names is
    ignored (and logged) rather than allowed to round-trip into os.environ.
    """
    filled = 0
    for cat_id in (SYSCFG_MODELS_ID, SYSCFG_FEATURES_ID):
        for mem in cortex.get_children(cat_id):
            key = mem.metadata.get("env_key")
            value = mem.metadata.get("env_value")
            if not key or value is None:
                continue
            if _is_safety_gate(key):
                # Defense-in-depth: even if someone pushes a safety gate
                # into the graph somehow, refuse to rehydrate it.
                import logging

                logging.getLogger(__name__).warning(
                    "env_sync: refused to rehydrate safety gate '%s' from graph; "
                    "file-system is the only source of truth for these flags",
                    key,
                )
                continue
            if key not in os.environ:
                os.environ[key] = str(value)
                filled += 1
    return filled


# ── Main entry point ───────────────────────────────────────────────────────────


def _cfg_files(instance_dir: Path) -> list[Path]:
    """
    Return the ordered list of cfg files to load for a given instance dir (D319).

    Load order (last wins): swarm.cfg → igor.cfg → igor.models.cfg →
    igor.switches.cfg → igor.credentials.cfg → igor.context.*.cfg (sorted)
    → igor.context.*.confidential.cfg (sorted)

    Returns only files that actually exist.
    """
    runtime_root = instance_dir.parent
    swarm_dir = runtime_root / "swarm"
    fixed_order = [
        swarm_dir / "swarm.cfg",
        instance_dir / "igor.cfg",
        instance_dir / "igor.models.cfg",
        instance_dir / "igor.switches.cfg",
        instance_dir / "igor.credentials.cfg",
    ]
    files = [p for p in fixed_order if p.exists()]
    files += sorted(instance_dir.glob("igor.context.*.cfg"))
    files += sorted(instance_dir.glob("igor.context.*.confidential.cfg"))
    return files


def boot_env_sync(cortex, instance_id: str, env_path: Path) -> None:
    """
    D119/D319 boot sequence:
      1. Hydrate os.environ from system config graph (fills missing vars)
      2. If cfg files exist: use their collective mtime to detect changes;
         re-read and push when any file is newer than last sync.
         Falls back to .env if no cfg files found (legacy / pre-migration).

    The env_path argument is kept for backward compatibility — it is used
    only as the fallback when no split cfg files exist.
    """
    import logging as _logging

    log = _logging.getLogger(__name__)

    # Step 1: hydrate from graph — no-op if graph is empty
    hydrate_from_graph(cortex)

    # Step 2: determine source files (cfg split preferred, .env fallback)
    instance_dir = env_path.parent
    cfg_files = _cfg_files(instance_dir)

    if cfg_files:
        # Collective mtime: max mtime across all cfg files
        try:
            cfg_mtime = max(p.stat().st_mtime for p in cfg_files)
        except Exception:
            return
        source_label = f"{len(cfg_files)} cfg files"
    elif env_path.exists():
        log.warning(
            "[env_sync] no split cfg files found — falling back to .env (pre-D319)"
        )
        cfg_files = [env_path]
        try:
            cfg_mtime = env_path.stat().st_mtime
        except Exception:
            return
        source_label = ".env (legacy)"
    else:
        return  # nothing to load

    instance_node = _ensure_instance_node(cortex, instance_id)
    last_mtime = float(instance_node.metadata.get("last_env_read_mtime", 0.0))

    if cfg_mtime <= last_mtime:
        return  # No change — graph is already up to date

    # Step 3: re-read all source files and push to graph (last wins, same as load_cfg)
    vars_dict: dict = {}
    for p in cfg_files:
        vars_dict.update(_parse_env_file(p))
    pushed = push_vars_to_graph(cortex, vars_dict)

    # Update instance node mtime
    instance_node.metadata["last_env_read_mtime"] = cfg_mtime
    cortex.store(instance_node)

    # Re-hydrate with newly pushed values
    hydrate_from_graph(cortex)

    log.info(
        f"[env_sync] synced {pushed} vars from {source_label} → graph "
        f"(instance={instance_id})"
    )


def load_igor_env_into_environ(
    instance_id: str | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, str]:
    """Hydrate os.environ from Igor's instance cfg files WITHOUT a DB connection.

    For standalone tools (ad-hoc REPL scripts, cert harnesses) that import
    cognition modules from a fresh Python process. Igor's main loop loads
    cfg via boot_env_sync(); standalone
    callers need this lighter path so routing matches autonomous behavior.

    Mirrors boot_env_sync's source ordering (last wins): swarm.cfg →
    igor.cfg → igor.models.cfg → igor.switches.cfg → igor.credentials.cfg
    → igor.context.*.cfg → igor.context.*.confidential.cfg.

    Returns the dict of vars that were applied (key → value).

    Args:
      instance_id: Igor instance id (e.g. "Igor-wild-0001"). When None,
        uses IGOR_INSTANCE_ID from os.environ, falling back to
        "Igor-wild-0001".
      overwrite: when False (default), pre-set os.environ values win
        (matches boot_env_sync's hydration priority); when True, cfg
        values overwrite existing os.environ entries.
    """
    if instance_id is None:
        instance_id = os.environ.get("IGOR_INSTANCE_ID", "Igor-wild-0001")

    runtime_root = Path.home() / ".TheIgors"
    instance_dir = runtime_root / instance_id
    if not instance_dir.is_dir():
        return {}

    cfg_files = _cfg_files(instance_dir)
    if not cfg_files:
        return {}

    vars_dict: dict[str, str] = {}
    for p in cfg_files:
        vars_dict.update(_parse_env_file(p))

    applied: dict[str, str] = {}
    for k, v in vars_dict.items():
        if not overwrite and k in os.environ:
            continue
        os.environ[k] = v
        applied[k] = v
    return applied
