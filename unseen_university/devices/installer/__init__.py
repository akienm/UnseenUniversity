"""Installer device — deploys unseen_university master skills to ~/.claude/skills/."""

from .shim import (
    DEFAULT_DEPLOY_TARGET,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_MASTER_ROOT,
    DeployResult,
    deploy_skills,
    deploy_status,
)

__all__ = [
    "DEFAULT_DEPLOY_TARGET",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_MASTER_ROOT",
    "DeployResult",
    "deploy_skills",
    "deploy_status",
]
