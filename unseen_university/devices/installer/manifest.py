"""
Skill manifest loader + validator.

The manifest is the source of truth for which skills the installer manages.
Skills not listed in the manifest are NOT touched on the deploy target — this
is how user-added local skills survive deploys.

Schema (JSON):
    {
      "version": 1,
      "skills": {
        "<skill-name>": {
          "category": "machine-agnostic" | "igor-specific",
          "machines": ["*"]  | ["host1", "host2"],  # "*" = all
          "deploy": true | false
        },
        ...
      }
    }

`category` is informational (helps reasoning about the skill set; doesn't
affect deploy behavior). `machines` filters per-host deploys. `deploy: false`
keeps a skill in the manifest for record-keeping but skips it on this run.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path

CURRENT_VERSION = 1


@dataclass
class SkillEntry:
    name: str
    category: str
    machines: list[str]
    deploy: bool

    def deploys_here(self, hostname: str | None = None) -> bool:
        if not self.deploy:
            return False
        if "*" in self.machines:
            return True
        host = hostname if hostname is not None else socket.gethostname()
        return host in self.machines


def load_manifest(path: Path) -> dict[str, SkillEntry]:
    """Load + validate manifest. Returns dict keyed by skill name."""
    raw = json.loads(path.read_text())
    if raw.get("version") != CURRENT_VERSION:
        raise ValueError(
            f"Manifest version {raw.get('version')} unsupported "
            f"(expected {CURRENT_VERSION})"
        )
    skills: dict[str, SkillEntry] = {}
    for name, spec in raw.get("skills", {}).items():
        skills[name] = SkillEntry(
            name=name,
            category=spec.get("category", "machine-agnostic"),
            machines=spec.get("machines", ["*"]),
            deploy=spec.get("deploy", True),
        )
    return skills
