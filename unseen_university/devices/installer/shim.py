"""
InstallerShim — orchestrates skill deploys from the master home in
unseen_university/skills/ to a target directory (default ~/.claude/skills/).

Per-skill triage lives in skills/manifest.json (which skills are managed,
which machines they deploy to). Skills present in the target dir but NOT
listed in the manifest are left alone — that's how user-added local skills
survive deploys.

Per-platform deploy primitives live in backends.py. The orchestrator picks
the right backend at runtime and treats each skill as one deploy unit.

Entry points:
  - deploy_skills(...)  — programmatic API
  - `agentctl skills deploy` — CLI
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from pathlib import Path

from .backends import DeployBackend, select_backend
from .manifest import SkillEntry, load_manifest

log = logging.getLogger(__name__)

DEFAULT_MASTER_ROOT = Path(__file__).resolve().parents[2] / "skills"
DEFAULT_DEPLOY_TARGET = Path.home() / ".claude" / "skills"
DEFAULT_MANIFEST_PATH = DEFAULT_MASTER_ROOT / "manifest.json"


@dataclass
class DeployResult:
    deployed: list[str]
    skipped_not_for_host: list[str]
    skipped_disabled: list[str]
    skipped_missing_source: list[str]
    untouched_local: list[str]


def deploy_skills(
    master_root: Path = DEFAULT_MASTER_ROOT,
    target: Path = DEFAULT_DEPLOY_TARGET,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    hostname: str | None = None,
    backend: DeployBackend | None = None,
) -> DeployResult:
    """Deploy manifest-listed skills from master_root to target.

    Skills present in target but not listed in the manifest are LEFT ALONE.
    That preserves user-added local skills.
    """
    skills = load_manifest(manifest_path)
    backend = backend or select_backend()
    host = hostname if hostname is not None else socket.gethostname()
    target.mkdir(parents=True, exist_ok=True)

    deployed: list[str] = []
    skipped_not_for_host: list[str] = []
    skipped_disabled: list[str] = []
    skipped_missing_source: list[str] = []

    for name, entry in skills.items():
        if not entry.deploy:
            skipped_disabled.append(name)
            continue
        if not entry.deploys_here(host):
            skipped_not_for_host.append(name)
            continue
        src = master_root / name
        if not src.is_dir():
            skipped_missing_source.append(name)
            log.warning("manifest lists %s but %s does not exist", name, src)
            continue
        dst = target / name
        backend.deploy_skill(src, dst)
        deployed.append(name)

    managed_names = set(skills.keys())
    untouched_local = [
        p.name for p in target.iterdir() if p.is_dir() and p.name not in managed_names
    ]

    return DeployResult(
        deployed=sorted(deployed),
        skipped_not_for_host=sorted(skipped_not_for_host),
        skipped_disabled=sorted(skipped_disabled),
        skipped_missing_source=sorted(skipped_missing_source),
        untouched_local=sorted(untouched_local),
    )


def deploy_status(
    master_root: Path = DEFAULT_MASTER_ROOT,
    target: Path = DEFAULT_DEPLOY_TARGET,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    hostname: str | None = None,
) -> dict:
    """Inspect target without deploying — what's managed, what's local-only."""
    skills = load_manifest(manifest_path)
    host = hostname if hostname is not None else socket.gethostname()
    managed = sorted(name for name, entry in skills.items() if entry.deploys_here(host))
    target_present = (
        sorted(p.name for p in target.iterdir() if p.is_dir())
        if target.exists()
        else []
    )
    return {
        "host": host,
        "manifest_path": str(manifest_path),
        "target": str(target),
        "managed_for_host": managed,
        "present_in_target": target_present,
        "local_only": sorted(set(target_present) - set(skills.keys())),
        "missing_in_target": sorted(set(managed) - set(target_present)),
    }
