"""Proof for T-skills-single-source-flip (D-skills-two-products).

The single-source cure rests on ONE behavioral claim: deploying a managed skill
LINKS it to the repo master rather than copying it — so the repo and
~/.claude/skills cannot drift (a stale copy is what broke day-close 2026-06-25).

This proof exercises only the stable `deploy_skills` API (present before and
after the change), so its red state is a real behavioral failure: the old
copy-backend produces a real directory (`is_symlink()` False) → red; the
symlink-backend produces a link → green. No net-new import, so the red is an
assertion about behavior, not a collection error.
"""
from __future__ import annotations

import json
from pathlib import Path

from unseen_university.devices.installer import deploy_skills


def test_deploy_links_managed_skill_to_master(tmp_path: Path):
    master = tmp_path / "master"
    master.mkdir()
    (master / "alpha").mkdir()
    (master / "alpha" / "SKILL.md").write_text("# alpha\n")

    manifest = master / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "alpha": {
                        "category": "machine-agnostic",
                        "machines": ["*"],
                        "deploy": True,
                    }
                },
            }
        )
    )

    target = tmp_path / "claude_skills"
    deploy_skills(master_root=master, target=target, manifest_path=manifest)

    # The cure: deploy LINKS, it does not COPY. A copy would be a real dir and
    # would drift; a link always resolves to the one canonical source.
    assert (target / "alpha").is_symlink(), "deploy must link the skill, not copy it"
    assert (target / "alpha").resolve() == (master / "alpha").resolve()
    assert (target / "alpha" / "SKILL.md").read_text() == "# alpha\n"
